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


def test_hysteresis_reduces_turnover():
    """Hysteresis should cut turnover materially when many moves are small."""
    # Slowly drifting position with high-freq jitter — exactly the case
    # hysteresis is built for.
    n = 1000
    base = np.sin(np.linspace(0, 4 * np.pi, n)) * 0.5
    rng = np.random.default_rng(7)
    jitter = rng.normal(0, 0.05, n)
    pos = _ts(base + jitter)
    underlying = _ts(rng.normal(0, 0.01, n))
    alphas = {"slow": pos}

    common = dict(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=False,
        enable_self_sharpe_gate=False,
        kill_drawdown=0.99,
        min_history=10,
        target_vol_annual=10.0,  # effectively disable vol targeting
        vol_lookback=20,
    )
    p_off = EnsembleAllocator(EnsembleConfig(**common, turnover_deadzone=0.0)).combine(alphas, underlying).target_position
    p_on = EnsembleAllocator(EnsembleConfig(**common, turnover_deadzone=0.15)).combine(alphas, underlying).target_position

    tov_off = float(np.abs(np.diff(p_off.values, prepend=0.0)).sum())
    tov_on = float(np.abs(np.diff(p_on.values, prepend=0.0)).sum())
    assert tov_on < tov_off * 0.5, f"hysteresis only cut turnover {tov_off:.2f}→{tov_on:.2f}"


def test_hysteresis_is_causal():
    """Mutating future returns must not change past hysteresis output."""
    rng = np.random.default_rng(11)
    n = 500
    underlying = _ts(rng.normal(0, 0.01, n))
    pos = _ts(rng.normal(0, 0.5, n))
    alphas = {"a": pos}
    cfg = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=False,
        enable_self_sharpe_gate=False,
        kill_drawdown=0.99,
        turnover_deadzone=0.05,
        min_history=10,
    )
    base = EnsembleAllocator(cfg).combine(alphas, underlying).target_position
    # Perturb a FUTURE bar (400). Bars [0..399] must be identical.
    underlying2 = underlying.copy()
    underlying2.iloc[400] = 99.0
    pert = EnsembleAllocator(cfg).combine(alphas, underlying2).target_position
    pd.testing.assert_series_equal(base.iloc[:400], pert.iloc[:400], check_names=False)


def test_long_kill_suppresses_dead_regime():
    """Long-kill should scale down hard when the 1-year rolling Sharpe is negative."""
    # Build a run where the first half is losing (alpha anti-aligned), second
    # half is winning. With long_kill on, the switch over to second half should
    # start suppressed (due to bad 1-year history) then recover.
    n = 3000
    rng = np.random.default_rng(42)
    underlying = _ts(rng.normal(0, 0.01, n))
    # First 1500 bars: bad; next 1500: good
    signs = np.where(np.arange(n) < n // 2, -np.sign(underlying.values), np.sign(underlying.values))
    alphas = {"swing": _ts(signs)}

    cfg_on = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=False,
        enable_self_sharpe_gate=False,
        enable_long_kill=True,
        long_kill_window=1500,
        long_kill_min_history=500,
        long_kill_floor=0.1,
        long_kill_full=0.3,
        long_kill_below=0.0,
        kill_drawdown=0.99,
        turnover_deadzone=0.0,
        min_history=10,
    )
    res = EnsembleAllocator(cfg_on).combine(alphas, underlying)
    abs_pos = res.target_position.abs()
    # Early-in-second-half must be suppressed (long history still bad)
    early_second = abs_pos.iloc[1600:1800].mean()
    # Late-in-second-half has accumulated good history → should be higher
    late_second = abs_pos.iloc[2700:2900].mean()
    assert late_second > early_second * 1.5, (
        f"long_kill did not recover: early={early_second:.3f} late={late_second:.3f}"
    )


def test_long_kill_is_causal():
    """Future returns must not change current long_kill multiplier."""
    n = 2000
    rng = np.random.default_rng(3)
    underlying = _ts(rng.normal(0, 0.01, n))
    alphas = {"a": _ts(rng.normal(0, 0.3, n))}
    cfg = EnsembleConfig(
        combine_mode="equal",
        periods_per_year=24 * 365,
        enable_alpha_gate=False,
        enable_self_sharpe_gate=False,
        enable_long_kill=True,
        long_kill_window=500,
        long_kill_min_history=200,
        kill_drawdown=0.99,
        turnover_deadzone=0.0,
        min_history=10,
    )
    base = EnsembleAllocator(cfg).combine(alphas, underlying).target_position
    underlying2 = underlying.copy()
    underlying2.iloc[1500] = 99.0  # perturb far future
    pert = EnsembleAllocator(cfg).combine(alphas, underlying2).target_position
    pd.testing.assert_series_equal(
        base.iloc[:1500], pert.iloc[:1500], check_names=False
    )


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
