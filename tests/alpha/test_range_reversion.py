"""Tests for range-bound mean-reversion alpha."""
import numpy as np
import pandas as pd
import pytest

from shared.alpha.base import AlphaConfig
from shared.alpha.range_reversion import RangeReversionAlpha


def _make_ranging_data(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic range-bound price data."""
    rng = np.random.default_rng(seed)
    # Mean-reverting prices around 100
    prices = [100.0]
    for _ in range(n - 1):
        shock = rng.normal(0, 0.5)
        reversion = -0.05 * (prices[-1] - 100)  # pull back to 100
        prices.append(prices[-1] + reversion + shock)
    close = np.array(prices)
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    volume = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.1, n),
        "high": high, "low": low, "close": close,
        "volume": volume,
    })


def _make_trending_data(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic trending price data."""
    rng = np.random.default_rng(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + 0.003 + rng.normal(0, 0.005)))  # strong uptrend
    close = np.array(prices)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    volume = rng.uniform(1e6, 5e6, n)
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.1, n),
        "high": high, "low": low, "close": close,
        "volume": volume,
    })


class TestRangeReversionAlpha:
    def test_output_shape(self):
        df = _make_ranging_data()
        alpha = RangeReversionAlpha()
        sig = alpha.generate(df)
        assert len(sig.position) == len(df)

    def test_bounded_output(self):
        df = _make_ranging_data()
        alpha = RangeReversionAlpha()
        sig = alpha.generate(df)
        assert sig.position.min() >= -1.0
        assert sig.position.max() <= 1.0

    def test_no_nan(self):
        df = _make_ranging_data()
        alpha = RangeReversionAlpha()
        sig = alpha.generate(df)
        assert sig.position.isna().sum() == 0

    def test_selective_by_design(self):
        """Alpha is very selective — mostly flat, only trades at extremes."""
        df = _make_ranging_data(n=800)
        alpha = RangeReversionAlpha()
        sig = alpha.generate(df)
        # MR signals are rare by design (extreme conditions only)
        active_pct = (sig.position.abs() > 0.01).mean()
        assert active_pct < 0.3, "Alpha should be selective, not always trading"

    def test_regime_score(self):
        """get_regime_score should return values between 0 and 1."""
        df = _make_ranging_data(n=500)
        alpha = RangeReversionAlpha()
        score = alpha.get_regime_score(df)
        assert len(score) == len(df)
        assert score.min() >= 0.0
        assert score.max() <= 1.0

    def test_regime_higher_in_range(self):
        """Regime score should be higher for ranging data than trending."""
        df_range = _make_ranging_data(n=500)
        df_trend = _make_trending_data(n=500)
        alpha = RangeReversionAlpha()
        score_range = alpha.get_regime_score(df_range).iloc[200:].mean()
        score_trend = alpha.get_regime_score(df_trend).iloc[200:].mean()
        assert score_range > score_trend, f"Range {score_range:.2f} should > Trend {score_trend:.2f}"

    def test_regime_detection_matters(self):
        """With regime gate disabled (adx_max=100), should be more active."""
        df = _make_ranging_data(n=500)
        alpha_strict = RangeReversionAlpha()
        alpha_loose = RangeReversionAlpha(AlphaConfig(
            name="range_reversion",
            params={"adx_max": 100, "bb_width_max_pct": 100, "min_regime_bars": 1}
        ))
        sig_strict = alpha_strict.generate(df)
        sig_loose = alpha_loose.generate(df)
        # Loose should be more active
        strict_active = (sig_strict.position.abs() > 0.01).sum()
        loose_active = (sig_loose.position.abs() > 0.01).sum()
        assert loose_active >= strict_active

    def test_custom_params(self):
        df = _make_ranging_data()
        alpha = RangeReversionAlpha(AlphaConfig(
            name="range_reversion",
            params={"adx_max": 15, "rsi_long_thr": 40, "rsi_short_thr": 60}
        ))
        sig = alpha.generate(df)
        assert len(sig.position) == len(df)

    def test_registry_integration(self):
        """Alpha should be accessible from registry."""
        from shared.alpha.registry import get_alpha
        alpha = get_alpha("range_reversion")
        assert isinstance(alpha, RangeReversionAlpha)
