"""Tests for capital preservation guards (A1-A5) and Kalman smoother (B1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.portfolio.signal_smoother import (
    KalmanConfig,
    ewma_smooth,
    kalman1d,
    smooth_alpha_positions,
)
from shared.risk.capital_guards import (
    CapitalGuards,
    GuardConfig,
    apply_guards_batch,
)


# ---- A1: absolute equity floor ----


def test_a1_halts_forever_below_floor():
    g = CapitalGuards(GuardConfig(absolute_floor_pct=0.5))
    # 60% loss in one bar → halt
    d = g.apply(last_pnl=-0.6)
    assert d.halted
    # Subsequent calls remain halted even with positive PnL
    d2 = g.apply(last_pnl=+0.9)
    assert d2.halted
    assert d2.scale == 0.0


def test_a1_peak_based_not_initial():
    g = CapitalGuards(GuardConfig(absolute_floor_pct=0.5))
    # Grow to equity 2.0 (peak)
    for _ in range(10):
        g.apply(last_pnl=0.072)  # ~ 2x over 10 bars
    # Drop 60% → equity ~0.8, peak ~2.0 → 40% of peak, should halt
    d = g.apply(last_pnl=-0.60)
    assert d.halted


# ---- A2: bar-loss cap ----


def test_a2_scales_down_after_big_bar():
    cfg = GuardConfig(
        enable_bar_loss_cap=True,
        bar_loss_pct=-0.03,
        bar_loss_pause_bars=5,
        bar_loss_scale=0.5,
    )
    g = CapitalGuards(cfg)
    d = g.apply(last_pnl=-0.04)
    scales: list[float] = []
    for _ in range(6):
        d = g.apply(last_pnl=0.0)
        scales.append(d.scale)
    assert scales[0] <= 0.5 + 0.01
    # After pause window, scale returns to 1.0
    for _ in range(20):
        g.apply(last_pnl=0.0001)
    d = g.apply(last_pnl=0.0001)
    assert d.scale > 0.9


# ---- A3: consecutive loss breaker ----


def test_a3_pauses_after_n_consecutive_losses():
    g = CapitalGuards(GuardConfig(enable_consec_breaker=True, consec_loss_count=3, consec_loss_pause_bars=5))
    for _ in range(3):
        g.apply(last_pnl=-0.001)
    d = g.apply(last_pnl=-0.001)
    assert d.scale == 0.0
    # A pause should end
    for _ in range(6):
        d = g.apply(last_pnl=0.0001)
    assert d.scale > 0.0


# ---- A4: vol leverage cap ----


def test_a4_shrinks_gross_when_realized_vol_exceeds_target():
    g = CapitalGuards(GuardConfig(enable_vol_leverage=True, vol_target_annual=0.10, vol_max_leverage=1.5))
    # Feed noisy returns (annualized vol ~0.3)
    rng = np.random.default_rng(0)
    for _ in range(200):
        g.apply(last_pnl=float(rng.normal(0, 0.01)))
    d = g.apply(last_pnl=float(rng.normal(0, 0.01)))
    # annualized vol ~ 0.01 * sqrt(24*365) ≈ 0.94; target 0.1 → leverage = 0.1/0.94 ≈ 0.11
    assert d.scale < 0.5
    assert d.realized_vol_ann > 0.1


# ---- A5: daily loss budget ----


def test_a5_freezes_when_day_breaches_budget():
    g = CapitalGuards(GuardConfig(enable_daily_budget=True, daily_budget_pct=-0.05))
    # Single bar of -6% should trip the day budget
    d = g.apply(last_pnl=-0.06)
    # scale should be 0 due to A5 (may also be 0 from A1/A2 but A5 specifically fires)
    assert d.scale == 0.0
    assert any("A5" in r for r in d.reasons)


# ---- batch API ----


def test_apply_guards_batch_stops_at_floor():
    # Benign series followed by crash
    pnl = [0.001] * 100 + [-0.6] + [0.001] * 50
    scales, diags = apply_guards_batch(pnl, GuardConfig(absolute_floor_pct=0.5))
    # Post-crash, every scale should be 0
    assert all(s == 0.0 for s in scales[101:])


# ---- B1: signal smoother ----


def test_kalman_smoother_reduces_noise_amplitude():
    rng = np.random.default_rng(7)
    signal = pd.Series(np.sin(np.linspace(0, 8 * np.pi, 400))).astype(float)
    noise = pd.Series(rng.normal(0, 0.5, 400))
    obs = signal + noise
    smoothed = kalman1d(obs, KalmanConfig(q=0.001, r=0.5))
    # Smoothed series should have lower variance than the raw obs
    assert smoothed.var() < obs.var()


def test_ewma_smooth_converges_to_constant():
    s = pd.Series([0.0] * 100 + [1.0] * 500)
    e = ewma_smooth(s, half_life=5)
    # Last value should be very close to 1.0
    assert abs(e.iloc[-1] - 1.0) < 0.01


def test_smooth_alpha_positions_preserves_keys_and_shape():
    idx = pd.date_range("2024-01-01", periods=300, freq="h")
    positions = {
        "a": pd.Series(np.random.default_rng(0).normal(0, 0.3, 300), index=idx),
        "b": pd.Series(np.random.default_rng(1).normal(0, 0.3, 300), index=idx),
    }
    out = smooth_alpha_positions(positions, method="kalman")
    assert set(out.keys()) == {"a", "b"}
    for name, s in out.items():
        assert len(s) == 300
