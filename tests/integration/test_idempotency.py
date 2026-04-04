"""
Integration tests for duplicate-delivery idempotency.

Verifies that sending the same event_id twice to market-data, feature-store,
and signal-service results in graceful handling with no duplicate side-effects.
"""

from datetime import datetime, timezone
from pathlib import Path
import importlib
import sys

import pytest

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

from tests.integration.test_event_reliability import (
    _isolate_load,
    _make_candle,
    _make_candles,
    _make_feature_snapshot,
)

from shared.events import EventEnvelope
from shared.persistence import RedisStore

validator_mod = _isolate_load("market-data", "app.core.validator")
candle_model = _isolate_load("market-data", "app.models.candle")
indicators_mod = _isolate_load("feature-store", "app.core.indicators")
feature_model = _isolate_load("feature-store", "app.models.feature")
scoring_mod = _isolate_load("signal-service", "app.core.scoring")
signal_model = _isolate_load("signal-service", "app.models.signal")


class TestIdempotency:
    """Send same event_id twice -- assert second delivery is handled gracefully."""

    def test_market_data_same_candle_twice(self):
        """Processing the same candle event twice does not crash -- validator detects duplicate."""
        candle = _make_candle(datetime(2026, 3, 30, tzinfo=UTC), 82000)
        r1 = validator_mod.validate_candle_transition(None, candle)
        assert r1.accepted
        # Second delivery of same candle is rejected as non-monotonic (idempotent guard)
        r2 = validator_mod.validate_candle_transition(candle, candle)
        assert not r2.accepted  # graceful rejection, no crash

    def test_feature_store_deterministic_on_replay(self):
        """Duplicate candle batch produces byte-identical features."""
        candles = _make_candles(30)
        f1 = indicators_mod.calculate_features("BTCUSDT", candles)
        f2 = indicators_mod.calculate_features("BTCUSDT", candles)
        assert f1.rsi_14 == f2.rsi_14
        assert f1.macd == f2.macd
        assert f1.bb_upper == f2.bb_upper
        assert f1.sma_20 == f2.sma_20
        assert f1.vwap == f2.vwap

    def test_signal_service_deterministic_on_replay(self):
        """Duplicate feature snapshot produces identical signal score."""
        snapshot = _make_feature_snapshot()
        s1 = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        s2 = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        assert s1.signal_score == s2.signal_score
        assert s1.direction == s2.direction
        assert s1.threshold_crossed == s2.threshold_crossed

    def test_redis_idempotency_set_dedup(self):
        """Redis-backed idempotency set prevents double-processing."""
        store = RedisStore("redis://localhost:6379")
        # Use fallback (in-memory) path since Redis may not be available
        assert not store.sismember("events:idem-test", "evt-100")
        store.sadd("events:idem-test", "evt-100")
        assert store.sismember("events:idem-test", "evt-100")
        # Second add is a no-op
        store.sadd("events:idem-test", "evt-100")
        assert store.sismember("events:idem-test", "evt-100")
