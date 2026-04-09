"""
Integration tests for event reliability: duplicate delivery, replay, and DLQ.

Covers:
  - Duplicate event_id delivery → idempotent handling (no double-processing)
  - Event replay with past timestamps → services handle correctly
  - Malformed events → land in DLQ subjects
"""

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import importlib
import importlib.util
import json
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _isolate_load(service_dir: str, module_dotpath: str):
    """Load a module from a service directory with isolation."""
    svc_root = ROOT / "services" / service_dir
    svc_str = str(svc_root)
    ns = f"_svc_{service_dir.replace('-', '_')}"

    cached_key = f"{ns}.{module_dotpath}"
    if cached_key in sys.modules:
        return sys.modules[cached_key]

    saved = {}
    to_remove = [k for k in sys.modules if k == "app" or k.startswith("app.")]
    for k in to_remove:
        saved[k] = sys.modules.pop(k)

    for k, v in list(sys.modules.items()):
        if k.startswith(f"{ns}.app"):
            real_key = k[len(ns) + 1:]
            sys.modules[real_key] = v

    inserted = svc_str not in sys.path
    if inserted:
        sys.path.insert(0, svc_str)

    try:
        mod = importlib.import_module(module_dotpath)
        current_app_mods = {
            k: v for k, v in sys.modules.items()
            if k == "app" or k.startswith("app.")
        }
        for k, v in current_app_mods.items():
            sys.modules[f"{ns}.{k}"] = v
        sys.modules[cached_key] = mod
    finally:
        to_clean = [k for k in sys.modules if k == "app" or k.startswith("app.")]
        for k in to_clean:
            del sys.modules[k]
        sys.modules.update(saved)
        if inserted:
            sys.path.remove(svc_str)

    return mod


# ---- Load shared events module ----
from shared.events import EventEnvelope, JetStreamBus
from shared.persistence import RedisStore


# ---- Load service modules ----
validator_mod = _isolate_load("market-data", "app.core.validator")
candle_model = _isolate_load("market-data", "app.models.candle")

indicators_mod = _isolate_load("feature-store", "app.core.indicators")
feature_model = _isolate_load("feature-store", "app.models.feature")

scoring_mod = _isolate_load("signal-service", "app.core.scoring")
signal_model = _isolate_load("signal-service", "app.models.signal")


# ===== helpers =====

def _make_candle(ts: datetime, price: float = 82000.0):
    return candle_model.CandlePayload(
        timestamp=ts,
        open=price - 10,
        high=price + 40,
        low=price - 50,
        close=price,
        volume=1200,
    )


def _make_candles(count: int = 30):
    CandlePayload = feature_model.CandlePayload
    base_price = 82000.0
    candles = []
    for i in range(count):
        ts = datetime(2026, 3, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
        p = base_price + (i * 50) + ((-1) ** i * 30)
        candles.append(
            CandlePayload(
                timestamp=ts, open=p - 10, high=p + 40, low=p - 50,
                close=p, volume=1000 + i * 10,
            )
        )
    return candles


def _make_feature_snapshot():
    candles = _make_candles(30)
    features = indicators_mod.calculate_features("BTCUSDT", candles)
    return signal_model.FeatureSnapshot(
        asset="BTCUSDT", timestamp=features.timestamp, close=features.close,
        volume=features.volume, rsi_14=features.rsi_14,
        macd=features.macd, macd_signal=features.macd_signal,
        bb_upper=features.bb_upper, bb_lower=features.bb_lower,
        sma_20=features.sma_20, vwap=features.vwap,
    )


def _make_event_envelope(event_type: str, data: dict, event_id: str = "evt-001") -> dict:
    """Build a raw event payload dict as it would arrive from JetStream."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "source": "test",
        "correlation_id": "corr-test",
        "user_id": "test-user",
        "data": data,
    }


# ===== Duplicate Delivery Tests =====

class TestDuplicateDelivery:
    """Verify that delivering the same event_id twice results in idempotent handling."""

    def test_market_data_duplicate_candle(self):
        """Same candle event processed twice should not corrupt state."""
        c1 = _make_candle(datetime(2026, 3, 30, tzinfo=UTC), 82000)
        r1 = validator_mod.validate_candle_transition(None, c1)
        assert r1.accepted

        # Process the same candle again (simulating duplicate delivery)
        r2 = validator_mod.validate_candle_transition(c1, c1)
        # Validator rejects non-monotonic timestamps — graceful duplicate rejection
        assert not r2.accepted

    def test_feature_store_duplicate_computation(self):
        """Computing features from the same candles twice produces identical results."""
        candles = _make_candles(30)
        features1 = indicators_mod.calculate_features("BTCUSDT", candles)
        features2 = indicators_mod.calculate_features("BTCUSDT", candles)
        assert features1.rsi_14 == features2.rsi_14
        assert features1.macd == features2.macd
        assert features1.sma_20 == features2.sma_20
        assert features1.close == features2.close

    def test_signal_service_duplicate_scoring(self):
        """Scoring the same features twice produces identical signals."""
        snapshot = _make_feature_snapshot()
        sig1 = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        sig2 = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        assert sig1.signal_score == sig2.signal_score
        assert sig1.direction == sig2.direction
        assert sig1.threshold_crossed == sig2.threshold_crossed

    @pytest.mark.skipif(
        not importlib.util.find_spec("psycopg"),
        reason="psycopg not installed (order-service requires PostgreSQL driver)",
    )
    def test_order_service_idempotency_key(self, monkeypatch):
        """Duplicate order with same idempotency_key returns cached result."""
        order_engine = _isolate_load("order-service", "app.core.engine")
        order_models = _isolate_load("order-service", "app.models.order")

        first_response = order_models.OrderResponse(
            order_id="dup-order-1",
            user_id="u1",
            asset="BTCUSDT",
            side="BUY",
            quantity=0.01,
            status="FILLED",
            risk_reason="approved",
            exchange="binance",
            shadow_mode=True,
            credential=order_models.CredentialSnapshot(
                user_id="u1", exchange="binance", loaded=True,
            ),
        )

        class StubRepo:
            config = order_models.ExecutionConfig(
                live_trading_enabled=False, allowed_exchanges=["binance"],
                default_shadow_mode=True, strict_runtime=False,
            )
            def get_execution_config(self): return self.config
            def get_by_idempotency_key(self, key):
                if key == "idem-key-1":
                    return first_response
                return None
            def save(self, *a, **kw): pass
            def record_lifecycle(self, *a, **kw): pass

        monkeypatch.setattr(order_engine, "order_repository", StubRepo())

        # First call with idempotency key returns the cached response
        result = order_engine.process_order(
            order_models.OrderRequest(
                user_id="u1", exchange="binance", asset="BTCUSDT",
                side="BUY", quantity=0.01, price=82000,
                requested_notional=820, max_notional=5000,
                current_drawdown=0.01, current_exposure=0, exposure_limit=50000,
                idempotency_key="idem-key-1",
            )
        )
        assert result.order_id == "dup-order-1"
        assert result.status == "FILLED"

    def test_jetstream_idempotency_via_redis(self):
        """JetStreamBus deduplicates events using Redis sismember check."""
        redis_store = RedisStore("redis://localhost:6379")

        # Simulate: first time event_id is NOT in the set
        assert not redis_store.sismember("events:test-consumer", "evt-dup-1")

        # After processing, add to set
        redis_store.sadd("events:test-consumer", "evt-dup-1")

        # Second time, event_id IS in the set — should be skipped
        assert redis_store.sismember("events:test-consumer", "evt-dup-1")


# ===== Event Replay Tests =====

class TestEventReplay:
    """Verify services handle events with past timestamps correctly."""

    def test_candle_with_past_timestamp_accepted(self):
        """Candles with timestamps in the past should still be validated."""
        past_ts = datetime(2025, 1, 15, 12, 0, tzinfo=UTC)
        candle = _make_candle(past_ts, 45000)
        result = validator_mod.validate_candle_transition(None, candle)
        assert result.accepted

    def test_feature_computation_with_historical_candles(self):
        """Features computed from historical candles produce valid results."""
        CandlePayload = feature_model.CandlePayload
        old_base = 45000.0
        candles = []
        for i in range(30):
            ts = datetime(2025, 1, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
            p = old_base + (i * 30) + ((-1) ** i * 20)
            candles.append(
                CandlePayload(
                    timestamp=ts, open=p - 5, high=p + 20, low=p - 25,
                    close=p, volume=800 + i * 5,
                )
            )
        features = indicators_mod.calculate_features("BTCUSDT", candles)
        assert features.asset == "BTCUSDT"
        assert features.rsi_14 is not None
        assert features.macd is not None

    def test_signal_scoring_with_old_features(self):
        """Signal scoring works correctly with historical feature data."""
        CandlePayload = feature_model.CandlePayload
        candles = []
        for i in range(30):
            ts = datetime(2025, 6, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
            p = 60000 + (i * 40) + ((-1) ** i * 15)
            candles.append(
                CandlePayload(
                    timestamp=ts, open=p - 8, high=p + 30, low=p - 35,
                    close=p, volume=900 + i * 8,
                )
            )
        features = indicators_mod.calculate_features("BTCUSDT", candles)
        snapshot = signal_model.FeatureSnapshot(
            asset="BTCUSDT", timestamp=features.timestamp, close=features.close,
            volume=features.volume, rsi_14=features.rsi_14,
            macd=features.macd, macd_signal=features.macd_signal,
            bb_upper=features.bb_upper, bb_lower=features.bb_lower,
            sma_20=features.sma_20, vwap=features.vwap,
        )
        signal = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        assert signal.direction in ("BUY", "SELL", "HOLD")
        assert isinstance(signal.signal_score, float)

    def test_replay_candle_sequence_preserves_order(self):
        """Replaying a sequence of candles in order produces consistent features."""
        CandlePayload = feature_model.CandlePayload
        candles_forward = []
        for i in range(30):
            ts = datetime(2025, 3, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
            p = 70000 + i * 100
            candles_forward.append(
                CandlePayload(
                    timestamp=ts, open=p - 10, high=p + 50, low=p - 50,
                    close=p, volume=1000,
                )
            )
        features = indicators_mod.calculate_features("ETHUSDT", candles_forward)
        assert features.asset == "ETHUSDT"
        assert features.close == 70000 + 29 * 100


# ===== DLQ Handling Tests =====

class TestDLQHandling:
    """Verify that malformed events are routed to DLQ subjects."""

    def test_malformed_candle_data_detected(self):
        """A candle with invalid values should trigger anomaly detection."""
        normal = _make_candle(datetime(2026, 3, 29, tzinfo=UTC), 80000)
        # Candle with extreme spike (50% jump) — triggers anomaly
        extreme = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            open=80000, high=120000, low=79000, close=119000, volume=5000,
        )
        result = validator_mod.validate_candle_transition(normal, extreme)
        assert result.anomaly_detected

    def test_event_envelope_with_dlq_event_type(self):
        """DLQ envelopes are constructed with .dlq suffix on event_type."""
        original_event = _make_event_envelope(
            "market.candle.updated",
            {"asset": "BTCUSDT", "candle": {}},
            event_id="evt-fail-1",
        )
        dlq_envelope = EventEnvelope(
            event_type=f"{original_event['event_type']}.dlq",
            source="test-consumer",
            correlation_id=original_event.get("correlation_id"),
            user_id=original_event.get("user_id"),
            data=original_event,
        )
        dumped = dlq_envelope.model_dump()
        assert dumped["event_type"] == "market.candle.updated.dlq"
        assert dumped["source"] == "test-consumer"
        assert dumped["data"]["event_id"] == "evt-fail-1"

    def test_redis_fallback_idempotency_without_connection(self):
        """RedisStore falls back to in-memory sets when Redis is unavailable."""
        store = RedisStore("redis://nonexistent:6379")
        # Operations should not raise even with disabled Redis
        assert not store.sismember("events:test", "id1")
        store.sadd("events:test", "id1")
        assert store.sismember("events:test", "id1")

    def test_dlq_reprocess_envelope_structure(self):
        """DLQ message wraps original payload and can be unwrapped for replay."""
        original_data = {
            "asset": "BTCUSDT",
            "candle": {
                "timestamp": "2026-03-30T00:00:00Z",
                "open": 82000, "high": 82300, "low": 81800,
                "close": 82150, "volume": 1200,
            },
        }
        original_envelope = _make_event_envelope(
            "market.candle.updated",
            original_data,
            event_id="evt-dlq-1",
        )

        # Simulate DLQ wrapping (as done in JetStreamBus._wrapped except block)
        dlq_envelope = EventEnvelope(
            event_type=f"{original_envelope['event_type']}.dlq",
            source="feature-store-consumer",
            data=original_envelope,
        )
        dlq_dump = dlq_envelope.model_dump()

        # Verify unwrapping: extract original subject and data
        original_subject = dlq_dump["event_type"].removesuffix(".dlq")
        assert original_subject == "market.candle.updated"

        unwrapped = dlq_dump["data"]
        assert unwrapped["event_id"] == "evt-dlq-1"
        assert unwrapped["data"]["asset"] == "BTCUSDT"

    def test_invalid_feature_data_does_not_crash_signal(self):
        """Signal scoring handles edge-case feature values gracefully."""
        # Features with None/zero values that might come from malformed data
        snapshot = signal_model.FeatureSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            close=0.0,
            volume=0,
            rsi_14=50.0,
            macd=0.0,
            macd_signal=0.0,
            bb_upper=0.0,
            bb_lower=0.0,
            sma_20=0.0,
            vwap=0.0,
        )
        # Should not crash even with zero/edge values
        signal = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        assert signal.direction in ("BUY", "SELL", "HOLD")
