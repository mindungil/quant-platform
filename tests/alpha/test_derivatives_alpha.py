"""Tests for DerivativesAlpha."""
import numpy as np
import pandas as pd
import pytest

from shared.alpha.base import AlphaConfig
from shared.alpha.derivatives_alpha import DerivativesAlpha


def _make_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.005, n)),
        "high": close * (1 + rng.uniform(0, 0.02, n)),
        "low": close * (1 - rng.uniform(0, 0.02, n)),
        "close": close,
        "volume": rng.uniform(100, 10000, n),
    }, index=idx)


def _make_derivatives(index, seed=42):
    rng = np.random.default_rng(seed)
    n = len(index)
    return {
        "open_interest": pd.DataFrame({
            "sumOpenInterest": 50000 + np.cumsum(rng.normal(0, 100, n)),
            "sumOpenInterestValue": 1e9 + np.cumsum(rng.normal(0, 1e6, n)),
        }, index=index),
        "global_lsr": pd.DataFrame({
            "longShortRatio": 1.0 + rng.normal(0, 0.3, n),
            "longAccount": 0.5 + rng.normal(0, 0.05, n),
            "shortAccount": 0.5 + rng.normal(0, 0.05, n),
        }, index=index),
        "top_lsr": pd.DataFrame({
            "longShortRatio": 1.0 + rng.normal(0, 0.2, n),
        }, index=index),
        "taker": pd.DataFrame({
            "buySellRatio": 1.0 + rng.normal(0, 0.2, n),
            "buyVol": rng.uniform(1000, 5000, n),
            "sellVol": rng.uniform(1000, 5000, n),
        }, index=index),
    }


class TestDerivativesAlpha:

    def test_smoke_with_data(self):
        df = _make_ohlcv()
        deriv = _make_derivatives(df.index)
        alpha = DerivativesAlpha(derivatives_data=deriv)
        sig = alpha.generate(df)
        assert len(sig.position) == len(df)
        assert sig.position.abs().max() <= 1.0

    def test_bounded(self):
        df = _make_ohlcv()
        deriv = _make_derivatives(df.index)
        alpha = DerivativesAlpha(derivatives_data=deriv)
        sig = alpha.generate(df)
        assert sig.position.min() >= -1.0
        assert sig.position.max() <= 1.0

    def test_not_all_flat(self):
        df = _make_ohlcv()
        deriv = _make_derivatives(df.index)
        alpha = DerivativesAlpha(derivatives_data=deriv)
        sig = alpha.generate(df)
        assert sig.position.abs().sum() > 0

    def test_graceful_without_data(self):
        df = _make_ohlcv()
        alpha = DerivativesAlpha(derivatives_data={})
        sig = alpha.generate(df)
        assert sig.position.abs().sum() == 0  # all flat

    def test_partial_data(self):
        """Should work with only some data types available."""
        df = _make_ohlcv()
        deriv = _make_derivatives(df.index)
        # Only OI data
        alpha = DerivativesAlpha(derivatives_data={"open_interest": deriv["open_interest"]})
        sig = alpha.generate(df)
        assert sig.position.abs().sum() > 0

    def test_registered(self):
        from shared.alpha.registry import ALPHA_REGISTRY
        assert "derivatives_alpha" in ALPHA_REGISTRY
