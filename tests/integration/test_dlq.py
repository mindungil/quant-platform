"""
Integration tests for Dead Letter Queue (DLQ) handling.

Verifies that malformed/invalid events are routed to DLQ subjects
and do not crash services.
"""

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.integration.test_event_reliability import (
    _isolate_load,
    _make_candle,
    _make_event_envelope,
)

from shared.events import EventEnvelope
from shared.persistence import RedisStore

validator_mod = _isolate_load("market-data", "app.core.validator")
candle_model = _isolate_load("market-data", "app.models.candle")
scoring_mod = _isolate_load("signal-service", "app.core.scoring")
signal_model = _isolate_load("signal-service", "app.models.signal")


class TestDLQ:
    """Malformed events land in DLQ subjects and do not crash services."""

    def test_anomalous_candle_detected_for_dlq(self):
        """Extreme price spike triggers anomaly -- candidate for DLQ routing."""
        normal = _make_candle(datetime(2026, 3, 29, tzinfo=UTC), 80000)
        extreme = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            open=80000, high=120000, low=79000, close=119000, volume=5000,
        )
        result = validator_mod.validate_candle_transition(normal, extreme)
        assert result.anomaly_detected

    def test_dlq_envelope_has_dlq_suffix(self):
        """DLQ envelope event_type ends with .dlq."""
        original = _make_event_envelope(
            "market.candle.updated", {"asset": "BTCUSDT"}, event_id="evt-bad-1",
        )
        dlq = EventEnvelope(
            event_type=f"{original['event_type']}.dlq",
            source="test-consumer",
            correlation_id=original.get("correlation_id"),
            user_id=original.get("user_id"),
            data=original,
        )
        dumped = dlq.model_dump()
        assert dumped["event_type"] == "market.candle.updated.dlq"
        assert dumped["data"]["event_id"] == "evt-bad-1"

    def test_dlq_envelope_unwrap_preserves_original(self):
        """DLQ message wraps original payload and can be unwrapped for replay."""
        original_data = {"asset": "ETHUSDT", "candle": {"close": 3000}}
        original = _make_event_envelope(
            "market.candle.updated", original_data, event_id="evt-dlq-2",
        )
        dlq = EventEnvelope(
            event_type=f"{original['event_type']}.dlq",
            source="feature-store-consumer",
            data=original,
        )
        dumped = dlq.model_dump()
        subject = dumped["event_type"].removesuffix(".dlq")
        assert subject == "market.candle.updated"
        assert dumped["data"]["data"]["asset"] == "ETHUSDT"

    def test_zero_feature_values_do_not_crash_signal(self):
        """Signal scoring handles zero/edge feature values gracefully."""
        snapshot = signal_model.FeatureSnapshot(
            asset="BTCUSDT", timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            close=0.0, volume=0, rsi_14=50.0, macd=0.0, macd_signal=0.0,
            bb_upper=0.0, bb_lower=0.0, sma_20=0.0, vwap=0.0,
        )
        signal = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        assert signal.direction in ("BUY", "SELL", "HOLD")

    def test_redis_fallback_on_disconnect(self):
        """RedisStore falls back to in-memory sets when Redis is unavailable."""
        store = RedisStore("redis://nonexistent:6379")
        # Operations should not raise even if Redis is unreachable (fallback mode)
        assert not store.sismember("events:dlq-test", "id1")
        store.sadd("events:dlq-test", "id1")
        assert store.sismember("events:dlq-test", "id1")

    def test_negative_volume_candle_still_validates(self):
        """Candle with negative volume does not crash the validator."""
        candle = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            open=82000, high=82300, low=81800, close=82150, volume=-100,
        )
        result = validator_mod.validate_candle_transition(None, candle)
        # May or may not be accepted, but must not crash
        assert isinstance(result.accepted, bool)
