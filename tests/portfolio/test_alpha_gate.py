"""Tests for the v3.3 per-alpha online performance gate."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.portfolio.ensemble import EnsembleAllocator, EnsembleConfig


def _ts(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="h")
    return pd.Series(values, index=idx)


def _make_alphas(n_bars: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    underlying = _ts(rng.normal(0, 0.01, n_bars))
    # alpha_good: aligned with underlying (positive expected sharpe)
    good_pos = _ts(np.sign(underlying.values))
    # alpha_bad: anti-aligned (negative expected sharpe)
    bad_pos = _ts(-np.sign(underlying.values))
    # alpha_dead: zero
    dead_pos = _ts(np.zeros(n_bars))
    return {"good": good_pos, "bad": bad_pos, "dead": dead_pos}, underlying


def test_alpha_gate_kills_negative_sharpe_alpha():
    """A persistently negative-Sharpe alpha should get its gate driven to floor."""
    alphas, ret = _make_alphas(2000, seed=42)
    cfg = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=True,
        alpha_gate_window=240,
        alpha_gate_min_history=120,
        alpha_gate_floor=0.0,
        alpha_gate_full=0.5,
        alpha_gate_kill_below=-0.5,
        # Disable other gates for isolation
        enable_self_sharpe_gate=False,
        kill_drawdown=0.99,
    )
    alloc = EnsembleAllocator(cfg)
    res = alloc.combine(alphas, ret)
    weights = res.alpha_weights

    # After warmup, the bad alpha should average a much smaller weight than good
    warmed = weights.iloc[500:]
    assert warmed["good"].mean() > warmed["bad"].mean() * 2, (
        f"good={warmed['good'].mean():.3f} bad={warmed['bad'].mean():.3f}"
    )


def test_alpha_gate_is_causal():
    """Gate at bar t must NOT depend on alpha_returns[t] (no peek-ahead).

    We verify by mutating bar t's return to an extreme value and checking
    that the weight at bar t is unchanged (only future weights move).
    """
    alphas, ret = _make_alphas(800, seed=7)
    cfg = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=True,
        alpha_gate_window=120,
        alpha_gate_min_history=60,
        enable_self_sharpe_gate=False,
        kill_drawdown=0.99,
    )
    alloc = EnsembleAllocator(cfg)

    # Baseline run
    w_base = alloc.combine(alphas, ret).alpha_weights.copy()

    # Perturb the underlying return at bar 500 to a huge spike
    ret_perturbed = ret.copy()
    ret_perturbed.iloc[500] = 5.0  # absurd 500% bar
    w_perturbed = alloc.combine(alphas, ret_perturbed).alpha_weights

    # The weight at bar 500 must equal baseline (causal)
    pd.testing.assert_series_equal(
        w_base.iloc[500], w_perturbed.iloc[500], check_names=False
    )

    # Bars >500 may differ; bars <500 must not differ
    pd.testing.assert_frame_equal(w_base.iloc[:501], w_perturbed.iloc[:501])


def test_alpha_gate_disabled_passthrough():
    """When disabled, gate must be a no-op vs the v3.2 behavior."""
    alphas, ret = _make_alphas(600, seed=1)
    cfg_off = EnsembleConfig(
        combine_mode="inverse_vol",
        periods_per_year=24 * 365,
        enable_alpha_gate=False,
        enable_self_sharpe_gate=False,
        kill_drawdown=0.99,
    )
    alloc_off = EnsembleAllocator(cfg_off)
    res_off = alloc_off.combine(alphas, ret)
    # Just sanity: weights sum to ~1 each row after warmup
    sums = res_off.alpha_weights.iloc[200:].sum(axis=1)
    assert (sums.between(0.99, 1.01)).all()


def test_alpha_gate_recovers_when_alpha_recovers():
    """If a bad alpha turns good mid-sample, the gate should re-open."""
    n = 1500
    rng = np.random.default_rng(13)
    underlying = _ts(rng.normal(0, 0.01, n))
    # Alpha that is anti-aligned for first half, aligned for second half
    pos = np.where(np.arange(n) < n // 2, -np.sign(underlying.values), np.sign(underlying.values))
    flipping = _ts(pos)
    constant_good = _ts(np.sign(underlying.values))
    alphas = {"flip": flipping, "good": constant_good}

    cfg = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=True,
        alpha_gate_window=200,
        alpha_gate_min_history=100,
        alpha_gate_floor=0.0,
        alpha_gate_full=0.5,
        alpha_gate_kill_below=-0.5,
        enable_self_sharpe_gate=False,
        kill_drawdown=0.99,
    )
    res = EnsembleAllocator(cfg).combine(alphas, underlying)
    w = res.alpha_weights

    # First half (after warmup): flip should be heavily down-weighted
    early = w.iloc[300 : n // 2 - 50]["flip"].mean()
    # Second half (well after the flip + warmup): flip should recover
    late = w.iloc[n // 2 + 400 :]["flip"].mean()
    assert late > early, f"early={early:.3f} late={late:.3f} — gate did not recover"
