"""Tests for the automated feature engine."""
import numpy as np
import pandas as pd
import pytest

from shared.features.engine import FeatureEngine, FeatureEngineConfig, FeatureMatrix


def _make_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.uniform(100, 10000, n)
    taker_buy = volume * rng.uniform(0.3, 0.7, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "taker_buy_base": taker_buy,
    }, index=idx)


def _make_funding(index: pd.DatetimeIndex, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0, 0.0001, len(index)), index=index, name="funding")


class TestFeatureEngine:

    def test_generates_features(self):
        df = _make_ohlcv()
        engine = FeatureEngine()
        fm = engine.generate(df)
        assert isinstance(fm, FeatureMatrix)
        assert len(fm.metadata) > 0
        assert fm.features.shape[0] == len(df)
        assert fm.features.shape[1] == len(fm.metadata)

    def test_feature_count_above_100(self):
        """Should produce at least 100 features from full OHLCV."""
        df = _make_ohlcv()
        fm = FeatureEngine().generate(df, _make_funding(df.index))
        assert fm.features.shape[1] >= 100, f"Only {fm.features.shape[1]} features"

    def test_no_nan_in_output(self):
        df = _make_ohlcv()
        fm = FeatureEngine().generate(df)
        assert not fm.features.isna().any().any(), "NaN found in features"

    def test_no_inf_in_output(self):
        df = _make_ohlcv()
        fm = FeatureEngine().generate(df)
        assert not np.isinf(fm.features.values).any(), "Inf found in features"

    def test_values_clipped(self):
        df = _make_ohlcv()
        cfg = FeatureEngineConfig(clip_value=5.0)
        fm = FeatureEngine(cfg).generate(df)
        assert fm.features.max().max() <= 5.0
        assert fm.features.min().min() >= -5.0

    def test_no_lookahead(self):
        """Perturbing future data should not change past features."""
        df = _make_ohlcv(n=2000)
        engine = FeatureEngine()
        fm1 = engine.generate(df)

        # Perturb last 200 bars
        df2 = df.copy()
        df2.iloc[-200:, df2.columns.get_loc("close")] *= 2.0
        df2.iloc[-200:, df2.columns.get_loc("high")] *= 2.0
        df2.iloc[-200:, df2.columns.get_loc("low")] *= 2.0
        fm2 = engine.generate(df2)

        # First 1000 bars should be identical (allowing warmup buffer)
        check_end = 1000
        diff = (fm1.features.iloc[:check_end] - fm2.features.iloc[:check_end]).abs().max().max()
        assert diff < 1e-10, f"Look-ahead detected: max diff = {diff}"

    def test_metadata_consistency(self):
        df = _make_ohlcv()
        fm = FeatureEngine().generate(df)
        assert len(fm.metadata) == fm.features.shape[1]
        assert set(fm.feature_names) == set(fm.features.columns)
        for m in fm.metadata:
            assert m.lookback > 0
            assert m.category in ("momentum", "mean_rev", "vol", "micro", "volume", "funding", "sentiment")

    def test_with_funding(self):
        df = _make_ohlcv()
        funding = _make_funding(df.index)
        fm_no = FeatureEngine().generate(df)
        fm_yes = FeatureEngine().generate(df, funding)
        # Should have more features with funding
        assert fm_yes.features.shape[1] > fm_no.features.shape[1]
        funding_feats = [m for m in fm_yes.metadata if m.category == "funding"]
        assert len(funding_feats) >= 5

    def test_without_taker_buy(self):
        """Should work without taker_buy_base column."""
        df = _make_ohlcv().drop(columns=["taker_buy_base"])
        fm = FeatureEngine().generate(df)
        assert fm.features.shape[1] >= 70

    def test_generate_panel(self):
        df1 = _make_ohlcv(seed=1)
        df2 = _make_ohlcv(seed=2)
        panel = {"BTC": df1, "ETH": df2}
        result = FeatureEngine().generate_panel(panel)
        assert set(result.keys()) == {"BTC", "ETH"}
        assert result["BTC"].features.shape == result["ETH"].features.shape

    def test_max_lookback(self):
        df = _make_ohlcv()
        fm = FeatureEngine().generate(df)
        assert fm.max_lookback > 0
        assert fm.max_lookback <= 1500

    def test_near_constant_removed(self):
        """Features with zero variance should be removed."""
        df = _make_ohlcv()
        # Make volume constant → volume zscore features become ~0
        df["volume"] = 1000.0
        fm = FeatureEngine().generate(df)
        for m in fm.metadata:
            col = fm.features[m.name]
            assert col.var() > 1e-10, f"Near-constant feature not removed: {m.name}"
