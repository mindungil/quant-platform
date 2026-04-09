"""Tests for the multi-strategy ensemble allocator."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha import get_alpha
from shared.backtest.synthetic import generate_synthetic_ohlcv
from shared.portfolio import EnsembleAllocator, EnsembleConfig


@pytest.fixture
def df_and_alphas():
    df = generate_synthetic_ohlcv(n_bars=2500, seed=7, trend_strength=8.0)
    alpha_pos = {}
    for name in ["trend_breakout", "momentum_ensemble", "mean_reversion"]:
        alpha_pos[name] = get_alpha(name).generate(df).position
    return df, alpha_pos


@pytest.mark.parametrize("mode", ["equal", "inverse_vol", "hrp"])
def test_ensemble_combine_modes(df_and_alphas, mode):
    df, alpha_pos = df_and_alphas
    ret = df["close"].pct_change().fillna(0.0)
    alloc = EnsembleAllocator(EnsembleConfig(combine_mode=mode, periods_per_year=24 * 365))
    res = alloc.combine(alpha_pos, ret)
    assert len(res.target_position) == len(ret)
    assert res.target_position.between(-1.0, 1.0).all()


def test_ensemble_kill_switch_zeros_after_drawdown(df_and_alphas):
    df, alpha_pos = df_and_alphas
    ret = df["close"].pct_change().fillna(0.0)
    cfg = EnsembleConfig(
        combine_mode="equal",
        kill_drawdown=0.001,  # almost any drawdown triggers
        kill_window=10,
        periods_per_year=24 * 365,
    )
    alloc = EnsembleAllocator(cfg)
    res = alloc.combine(alpha_pos, ret)
    # After triggering, the kill switch should zero some positions
    assert (res.target_position == 0).any()


def test_ensemble_max_per_alpha_cap(df_and_alphas):
    df, alpha_pos = df_and_alphas
    ret = df["close"].pct_change().fillna(0.0)
    alloc = EnsembleAllocator(EnsembleConfig(max_per_alpha=0.4, periods_per_year=24 * 365))
    res = alloc.combine(alpha_pos, ret)
    assert (res.alpha_weights <= 0.4 + 1e-9).all().all()


def test_ensemble_handles_no_alphas():
    alloc = EnsembleAllocator()
    res = alloc.combine({}, pd.Series(dtype=float))
    assert len(res.target_position) == 0
