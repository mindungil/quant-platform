"""Tests for the in-process backtest runner and metrics."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from shared.alpha import TrendBreakoutAlpha, MeanReversionAlpha, get_alpha
from shared.backtest import (
    BacktestRunner,
    CostModel,
    LIVE_THRESHOLDS,
    SEED_THRESHOLDS,
    generate_ranging_ohlcv,
    generate_synthetic_ohlcv,
    run_backtest,
    walk_forward,
)
from shared.backtest.metrics import (
    all_metrics,
    deflated_sharpe_ratio,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)


# ----- metrics -----


def test_sharpe_zero_for_constant_returns():
    assert sharpe_ratio([0.001] * 100) == 0.0


def test_sharpe_positive_for_positive_drift():
    rng = np.random.default_rng(42)
    rets = 0.001 + 0.01 * rng.standard_normal(500)
    assert sharpe_ratio(rets, periods_per_year=252) > 0


def test_max_drawdown_geq_zero():
    rets = [-0.1, 0.05, -0.05, 0.02, -0.08]
    dd = max_drawdown(rets)
    assert dd > 0


def test_profit_factor_no_losses_is_inf():
    assert profit_factor([0.01, 0.02, 0.005]) == float("inf")


def test_profit_factor_no_wins_is_zero():
    assert profit_factor([-0.01, -0.02]) == 0.0


def test_sortino_ignores_upside_vol():
    rng = np.random.default_rng(7)
    sym = rng.standard_normal(500) * 0.01
    upside = sym.copy()
    upside[upside > 0] *= 5  # huge wins, no extra losses → Sortino should be much higher
    so_sym = sortino_ratio(sym)
    so_up = sortino_ratio(upside)
    assert so_up > so_sym


def test_deflated_sharpe_no_penalty_at_n_trials_1():
    # With n_trials=1, the multiple-testing penalty is zero, so DSR is
    # just the prob the observed Sharpe is significantly > 0.
    p = deflated_sharpe_ratio(2.0, n_observations=500, n_trials=1)
    assert 0.5 <= p <= 1.0


def test_deflated_sharpe_penalty_grows_with_trials():
    p1 = deflated_sharpe_ratio(1.0, n_observations=500, n_trials=1)
    p100 = deflated_sharpe_ratio(1.0, n_observations=500, n_trials=100)
    assert p100 < p1  # more trials → harder to be significant


def test_all_metrics_handles_empty():
    m = all_metrics([])
    assert m["sharpe"] == 0.0
    assert m["n_obs"] == 0


# ----- runner -----


def test_runner_passes_trend_alpha_on_trending_data():
    df = generate_synthetic_ohlcv(n_bars=4000, seed=7, trend_strength=8.0)
    rep = run_backtest(TrendBreakoutAlpha(), df, periods_per_year=24 * 365)
    assert rep.metrics["sharpe"] != 0  # non-trivial result
    assert len(rep.equity_curve) == len(df)


def test_runner_metrics_match_dictlike():
    df = generate_synthetic_ohlcv(n_bars=2000, seed=11, trend_strength=8.0)
    rep = run_backtest(TrendBreakoutAlpha(), df, periods_per_year=24 * 365)
    blob = rep.to_dict()
    assert blob["status"] in {"PASSED", "FAILED"}
    assert "metrics" in blob
    assert "n_obs" in blob["metrics"]


def test_runner_costs_reduce_returns():
    df = generate_synthetic_ohlcv(n_bars=2000, seed=23, trend_strength=8.0)
    no_cost = run_backtest(
        TrendBreakoutAlpha(),
        df,
        cost_model=CostModel(commission_bps=0.0, slippage_bps=0.0, impact_coef=0.0),
        periods_per_year=24 * 365,
    )
    high_cost = run_backtest(
        TrendBreakoutAlpha(),
        df,
        cost_model=CostModel(commission_bps=50.0, slippage_bps=20.0, impact_coef=2.0),
        periods_per_year=24 * 365,
    )
    assert high_cost.metrics["total_return"] < no_cost.metrics["total_return"]


def test_seed_thresholds_looser_than_live():
    assert SEED_THRESHOLDS["sharpe_min"] < LIVE_THRESHOLDS["sharpe_min"]
    assert SEED_THRESHOLDS["max_drawdown_max"] >= LIVE_THRESHOLDS["max_drawdown_max"]


# ----- walk-forward -----


def test_walk_forward_produces_oos_metrics():
    df = generate_synthetic_ohlcv(n_bars=4000, seed=7, trend_strength=8.0)
    res = walk_forward(TrendBreakoutAlpha(), df, n_windows=4, periods_per_year=24 * 365)
    assert res.n_windows >= 1
    assert "sharpe" in res.oos_aggregate
    assert isinstance(res.consistency_score, float)


def test_walk_forward_sharpe_decay_is_finite():
    df = generate_synthetic_ohlcv(n_bars=3000, seed=11, trend_strength=8.0)
    res = walk_forward(MeanReversionAlpha(), df, n_windows=3, periods_per_year=24 * 365)
    assert math.isfinite(res.sharpe_decay)
