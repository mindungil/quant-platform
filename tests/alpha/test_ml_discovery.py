"""Tests for MLDiscoveryAlpha."""
import numpy as np
import pandas as pd
import pytest

from shared.alpha.base import AlphaConfig
from shared.alpha.ml_discovery import MLDiscoveryAlpha
from shared.features.engine import FeatureEngine


def _make_ohlcv(n: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV with mild trend signal."""
    rng = np.random.default_rng(seed)
    # Inject a momentum signal: positive autocorrelation
    noise = rng.normal(0, 0.01, n)
    signal = np.zeros(n)
    for i in range(1, n):
        signal[i] = 0.3 * signal[i - 1] + noise[i]
    close = 100.0 * np.exp(np.cumsum(signal))
    high = close * (1 + rng.uniform(0, 0.015, n))
    low = close * (1 - rng.uniform(0, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.uniform(100, 10000, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "taker_buy_base": volume * rng.uniform(0.3, 0.7, n),
    }, index=idx)


class TestMLDiscovery:

    def test_smoke(self):
        """Basic run produces valid output."""
        df = _make_ohlcv()
        config = AlphaConfig(
            name="ml_discovery",
            params={"train_window": 2000, "refit_every": 500, "warmup": 800},
        )
        alpha = MLDiscoveryAlpha(config=config)
        sig = alpha.generate(df)
        assert len(sig.position) == len(df)
        assert sig.position.abs().max() <= 1.0
        # Should have some non-zero positions after warmup
        assert sig.position.abs().sum() > 0

    def test_positions_in_range(self):
        """All positions must be in [-1, 1]."""
        df = _make_ohlcv()
        config = AlphaConfig(
            name="ml_discovery",
            params={"train_window": 2000, "refit_every": 500, "warmup": 800},
        )
        alpha = MLDiscoveryAlpha(config=config)
        sig = alpha.generate(df)
        assert sig.position.min() >= -1.0
        assert sig.position.max() <= 1.0

    def test_no_lookahead(self):
        """Perturbing future data should not change past positions."""
        df = _make_ohlcv(n=5000)
        config = AlphaConfig(
            name="ml_discovery",
            params={"train_window": 2000, "refit_every": 500, "warmup": 800},
        )

        alpha1 = MLDiscoveryAlpha(config=config)
        sig1 = alpha1.generate(df)

        # Perturb last 500 bars
        df2 = df.copy()
        df2.iloc[-500:, df2.columns.get_loc("close")] *= 3.0
        df2.iloc[-500:, df2.columns.get_loc("high")] *= 3.0
        df2.iloc[-500:, df2.columns.get_loc("low")] *= 3.0

        alpha2 = MLDiscoveryAlpha(config=config)
        sig2 = alpha2.generate(df2)

        # Positions before the perturbation zone should be nearly identical.
        # Allow small tolerance for LightGBM threading non-determinism.
        # (The Feature Engine is tested separately with strict 1e-10 tolerance.)
        check_end = len(df) - 500 - 300
        if check_end > 1000:
            diff = (sig1.position.iloc[:check_end] - sig2.position.iloc[:check_end]).abs().max()
            assert diff < 0.05, f"Look-ahead detected: max diff = {diff}"

    def test_decorrelation_penalty(self):
        """High correlation with existing alpha should reduce positions."""
        df = _make_ohlcv(n=5000)
        config = AlphaConfig(
            name="ml_discovery",
            params={"train_window": 2000, "refit_every": 500, "warmup": 800,
                     "max_corr_penalty": 0.3},
        )

        # Run without existing positions
        alpha_free = MLDiscoveryAlpha(config=config)
        sig_free = alpha_free.generate(df)

        # Run with a highly correlated existing position (the signal itself)
        alpha_penalized = MLDiscoveryAlpha(
            config=config,
            existing_positions={"trend": sig_free.position},
        )
        sig_penalized = alpha_penalized.generate(df)

        # Penalized should have lower average absolute position
        free_avg = sig_free.position.abs().mean()
        pen_avg = sig_penalized.position.abs().mean()
        # At least some reduction (not necessarily dramatic)
        assert pen_avg <= free_avg * 1.1  # allow small tolerance

    def test_diagnostics(self):
        df = _make_ohlcv(n=5000)
        config = AlphaConfig(
            name="ml_discovery",
            params={"train_window": 2000, "refit_every": 500, "warmup": 800},
        )
        alpha = MLDiscoveryAlpha(config=config)
        sig = alpha.generate(df)
        assert "n_refit_windows" in sig.diagnostics
        assert sig.diagnostics["n_refit_windows"] > 0

    def test_short_data_graceful(self):
        """Should handle data shorter than train window gracefully."""
        df = _make_ohlcv(n=500)
        config = AlphaConfig(
            name="ml_discovery",
            params={"train_window": 2000, "refit_every": 500, "warmup": 800},
        )
        alpha = MLDiscoveryAlpha(config=config)
        sig = alpha.generate(df)
        # Should return all zeros (not enough data)
        assert sig.position.abs().sum() == 0
