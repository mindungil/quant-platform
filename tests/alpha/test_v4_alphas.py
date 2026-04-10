"""Smoke tests for v4 alphas: order_flow, lead_lag, vwap_reversion."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha.order_flow import OrderFlowAlpha
from shared.alpha.lead_lag import LeadLagAlpha
from shared.alpha.vwap_reversion import VWAPReversionAlpha


def _make_ohlcv(n: int = 2000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    volume = rng.uniform(100, 10000, n)
    taker_buy = volume * rng.uniform(0.3, 0.7, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.005, n)),
        "high": high, "low": low, "close": close,
        "volume": volume, "taker_buy_base": taker_buy,
    }, index=idx)


class TestOrderFlow:
    def test_runs_and_returns_series(self):
        df = _make_ohlcv()
        sig = OrderFlowAlpha().generate(df)
        assert isinstance(sig.position, pd.Series)
        assert len(sig.position) == len(df)

    def test_bounded(self):
        df = _make_ohlcv()
        pos = OrderFlowAlpha().generate(df).position
        assert pos.abs().max() <= 1.0 + 1e-9

    def test_no_taker_col_fallback(self):
        df = _make_ohlcv().drop(columns=["taker_buy_base"])
        sig = OrderFlowAlpha().generate(df)
        assert sig.position.abs().sum() > 0

    def test_not_all_flat(self):
        df = _make_ohlcv(2000, seed=42)
        pos = OrderFlowAlpha().generate(df).position
        assert (pos.abs() > 0.01).mean() > 0.3


class TestLeadLag:
    def test_flat_when_no_exog(self):
        df = _make_ohlcv()
        pos = LeadLagAlpha(exog=None).generate(df).position
        assert pos.abs().sum() == 0.0

    def test_nonflat_when_exog_provided(self):
        df = _make_ohlcv(2000, seed=1)
        btc = _make_ohlcv(2000, seed=99)
        pos = LeadLagAlpha(exog=btc).generate(df).position
        assert (pos.abs() > 0.01).mean() > 0.1

    def test_bounded(self):
        df = _make_ohlcv(2000, seed=2)
        btc = _make_ohlcv(2000, seed=3)
        pos = LeadLagAlpha(exog=btc).generate(df).position
        assert pos.abs().max() <= 1.0 + 1e-9


class TestVWAPReversion:
    def test_runs(self):
        df = _make_ohlcv()
        sig = VWAPReversionAlpha().generate(df)
        assert isinstance(sig.position, pd.Series)

    def test_bounded(self):
        df = _make_ohlcv()
        pos = VWAPReversionAlpha().generate(df).position
        assert pos.abs().max() <= 1.0 + 1e-9

    def test_not_all_flat(self):
        # Use a seed with more extreme deviations
        df = _make_ohlcv(3000, seed=7)
        pos = VWAPReversionAlpha().generate(df).position
        assert (pos.abs() > 0.01).sum() > 0

    def test_trend_gate_suppresses_in_trend(self):
        # Create a strong trend (50% up over 2000 bars)
        n = 2000
        rng = np.random.default_rng(5)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.005, n)))
        idx = pd.date_range("2024-01-01", periods=n, freq="h")
        df = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": rng.uniform(100, 10000, n),
        }, index=idx)
        pos = VWAPReversionAlpha().generate(df).position
        # In a strong trend, most bars should be near-flat due to gate
        assert pos.abs().mean() < 0.15
