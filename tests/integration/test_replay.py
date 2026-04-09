"""
Integration tests for event replay with older timestamps.

Verifies that services process events with past timestamps without error,
supporting historical data replay and backfill scenarios.
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
)

validator_mod = _isolate_load("market-data", "app.core.validator")
candle_model = _isolate_load("market-data", "app.models.candle")
indicators_mod = _isolate_load("feature-store", "app.core.indicators")
feature_model = _isolate_load("feature-store", "app.models.feature")
scoring_mod = _isolate_load("signal-service", "app.core.scoring")
signal_model = _isolate_load("signal-service", "app.models.signal")


class TestReplay:
    """Publish events with older timestamps -- verify services process without error."""

    def test_candle_with_2025_timestamp(self):
        """Candle from 2025 is accepted by validator."""
        candle = _make_candle(datetime(2025, 1, 15, 12, 0, tzinfo=UTC), 45000)
        result = validator_mod.validate_candle_transition(None, candle)
        assert result.accepted

    def test_candle_with_2024_timestamp(self):
        """Candle from 2024 is accepted by validator."""
        candle = _make_candle(datetime(2024, 6, 1, 0, 0, tzinfo=UTC), 38000)
        result = validator_mod.validate_candle_transition(None, candle)
        assert result.accepted

    def test_features_from_historical_candles(self):
        """Features computed from 2025 historical candles produce valid results."""
        CandlePayload = feature_model.CandlePayload
        candles = []
        for i in range(30):
            ts = datetime(2025, 1, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
            p = 45000 + (i * 30) + ((-1) ** i * 20)
            candles.append(CandlePayload(
                timestamp=ts, open=p - 5, high=p + 20, low=p - 25,
                close=p, volume=800 + i * 5,
            ))
        features = indicators_mod.calculate_features("BTCUSDT", candles)
        assert features.asset == "BTCUSDT"
        assert features.rsi_14 is not None
        assert features.macd is not None

    def test_signal_scoring_with_historical_features(self):
        """Signal scoring works with historical feature data from 2025."""
        CandlePayload = feature_model.CandlePayload
        candles = []
        for i in range(30):
            ts = datetime(2025, 6, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
            p = 60000 + (i * 40) + ((-1) ** i * 15)
            candles.append(CandlePayload(
                timestamp=ts, open=p - 8, high=p + 30, low=p - 35,
                close=p, volume=900 + i * 8,
            ))
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

    def test_replay_sequence_preserves_feature_order(self):
        """Replaying a monotonically increasing candle sequence yields correct close."""
        CandlePayload = feature_model.CandlePayload
        candles = []
        for i in range(24):
            ts = datetime(2025, 3, 1, i, 0, 0, tzinfo=UTC)
            p = 70000 + i * 100
            candles.append(CandlePayload(
                timestamp=ts, open=p - 10, high=p + 50, low=p - 50,
                close=p, volume=1000,
            ))
        # Add more candles on next day to reach 30
        for i in range(6):
            ts = datetime(2025, 3, 2, i, 0, 0, tzinfo=UTC)
            p = 70000 + (24 + i) * 100
            candles.append(CandlePayload(
                timestamp=ts, open=p - 10, high=p + 50, low=p - 50,
                close=p, volume=1000,
            ))
        features = indicators_mod.calculate_features("ETHUSDT", candles)
        assert features.asset == "ETHUSDT"
        assert features.close == 70000 + 29 * 100

    def test_replay_does_not_affect_different_asset(self):
        """Replaying BTC data does not contaminate ETH feature computation."""
        CandlePayload = feature_model.CandlePayload
        btc_candles = []
        eth_candles = []
        for i in range(24):
            ts = datetime(2025, 2, 1, i, 0, 0, tzinfo=UTC)
            btc_candles.append(CandlePayload(
                timestamp=ts, open=80000 + i * 10, high=80100 + i * 10,
                low=79900 + i * 10, close=80050 + i * 10, volume=1000,
            ))
            eth_candles.append(CandlePayload(
                timestamp=ts, open=3000 + i, high=3010 + i,
                low=2990 + i, close=3005 + i, volume=500,
            ))
        # Pad to 30 candles on next day
        for i in range(6):
            ts = datetime(2025, 2, 2, i, 0, 0, tzinfo=UTC)
            btc_candles.append(CandlePayload(
                timestamp=ts, open=80240 + i * 10, high=80340 + i * 10,
                low=80140 + i * 10, close=80290 + i * 10, volume=1000,
            ))
            eth_candles.append(CandlePayload(
                timestamp=ts, open=3024 + i, high=3034 + i,
                low=3014 + i, close=3029 + i, volume=500,
            ))
        btc_feat = indicators_mod.calculate_features("BTCUSDT", btc_candles)
        eth_feat = indicators_mod.calculate_features("ETHUSDT", eth_candles)
        assert btc_feat.asset == "BTCUSDT"
        assert eth_feat.asset == "ETHUSDT"
        assert btc_feat.close != eth_feat.close
