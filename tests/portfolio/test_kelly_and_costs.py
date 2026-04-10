"""Tests for half-Kelly sizing and funding cost model."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.portfolio.ensemble import EnsembleAllocator, EnsembleConfig
from shared.backtest.metrics import apply_transaction_costs, apply_funding_cost


def _ts(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="h")
    return pd.Series(values, index=idx)


class TestKellySizing:
    def test_half_kelly_produces_output(self):
        """Basic smoke: half_kelly sizing runs without error."""
        rng = np.random.default_rng(42)
        n = 1000
        underlying = _ts(rng.normal(0.0001, 0.01, n))
        alphas = {"trend": _ts(np.sign(underlying.values))}
        cfg = EnsembleConfig(
            combine_mode="equal",
            periods_per_year=24 * 365,
            sizing_mode="half_kelly",
            enable_alpha_gate=False,
            enable_self_sharpe_gate=False,
            kill_drawdown=0.99,
            turnover_deadzone=0.0,
            min_history=10,
        )
        res = EnsembleAllocator(cfg).combine(alphas, underlying)
        assert len(res.target_position) == n
        assert res.target_position.abs().max() <= 1.0 + 1e-9

    def test_half_kelly_scales_with_edge(self):
        """Kelly should size up when edge is strong, down when weak."""
        rng = np.random.default_rng(7)
        n = 2000
        # First half: strong signal (alpha aligned), second: random
        underlying = _ts(rng.normal(0, 0.01, n))
        pos_values = np.where(np.arange(n) < n // 2,
                              np.sign(underlying.values),
                              rng.choice([-1, 1], n))
        alphas = {"a": _ts(pos_values)}
        cfg = EnsembleConfig(
            combine_mode="equal",
            periods_per_year=24 * 365,
            sizing_mode="half_kelly",
            enable_alpha_gate=False,
            enable_self_sharpe_gate=False,
            kill_drawdown=0.99,
            turnover_deadzone=0.0,
            min_history=10,
        )
        res = EnsembleAllocator(cfg).combine(alphas, underlying)
        pos = res.target_position.abs()
        # After warmup, first half should have higher avg position than second
        first_half = pos.iloc[200:n // 2].mean()
        second_half = pos.iloc[n // 2 + 200:].mean()
        assert first_half > second_half, (
            f"Kelly should size up with edge: first={first_half:.3f} second={second_half:.3f}"
        )

    def test_vol_target_is_default(self):
        """Default sizing_mode should be vol_target, not kelly."""
        cfg = EnsembleConfig()
        assert cfg.sizing_mode == "vol_target"

    def test_kelly_vs_vol_target_different_output(self):
        """Kelly and vol_target should produce different position series."""
        rng = np.random.default_rng(99)
        n = 800
        underlying = _ts(rng.normal(0.0002, 0.01, n))
        alphas = {"a": _ts(np.tanh(rng.normal(0, 1, n)))}
        common = dict(
            combine_mode="equal",
            periods_per_year=24 * 365,
            enable_alpha_gate=False,
            enable_self_sharpe_gate=False,
            kill_drawdown=0.99,
            turnover_deadzone=0.0,
            min_history=10,
        )
        pos_vt = EnsembleAllocator(EnsembleConfig(**common, sizing_mode="vol_target")).combine(alphas, underlying).target_position
        pos_hk = EnsembleAllocator(EnsembleConfig(**common, sizing_mode="half_kelly")).combine(alphas, underlying).target_position
        # They should differ (not identical)
        assert not np.allclose(pos_vt.values[200:], pos_hk.values[200:], atol=1e-6)


class TestFundingCost:
    def test_apply_funding_cost_long_pays(self):
        """Positive funding + long position → cost (positive return from apply_funding_cost)."""
        pos = np.array([1.0, 1.0, 1.0])
        fr = np.array([0.001, 0.001, 0.001])  # positive funding
        cost = apply_funding_cost(pos, fr)
        assert (cost > 0).all()  # long pays when funding positive

    def test_apply_funding_cost_short_receives(self):
        """Positive funding + short position → income (negative from apply_funding_cost)."""
        pos = np.array([-1.0, -1.0])
        fr = np.array([0.001, 0.001])
        cost = apply_funding_cost(pos, fr)
        assert (cost < 0).all()  # short receives when funding positive

    def test_transaction_costs_with_funding(self):
        """apply_transaction_costs should subtract funding cost from PnL."""
        pos = np.array([1.0, 1.0, 1.0, 1.0])
        ret = np.array([0.01, 0.01, 0.01, 0.01])
        fr = np.array([0.001, 0.001, 0.001, 0.001])

        pnl_no_fund = apply_transaction_costs(pos, ret, cost_bps=0.0, funding_rate_per_bar=None)
        pnl_with_fund = apply_transaction_costs(pos, ret, cost_bps=0.0, funding_rate_per_bar=fr)

        # With funding, PnL should be lower (long pays positive funding)
        assert pnl_with_fund.sum() < pnl_no_fund.sum()

    def test_zero_funding_no_effect(self):
        """Zero funding rate should produce identical PnL."""
        pos = np.array([0.5, -0.3, 0.8])
        ret = np.array([0.01, -0.02, 0.005])
        fr = np.zeros(3)
        pnl_none = apply_transaction_costs(pos, ret, cost_bps=5.0)
        pnl_zero = apply_transaction_costs(pos, ret, cost_bps=5.0, funding_rate_per_bar=fr)
        np.testing.assert_array_almost_equal(pnl_none, pnl_zero)
