"""Tests for kill switch, concentration caps, funding-spike, drift monitor."""
from __future__ import annotations

import pytest

from shared.execution.drift_monitor import DriftConfig, DriftMonitor
from shared.risk.concentration import (
    ConcentrationConfig,
    apply_caps,
)
from shared.risk.funding_spike import FundingSpikeConfig, check_funding_spike
from shared.risk.kill_switch import (
    KillConfig,
    KillLevel,
    apply_kill_level,
    blocks_new_gross,
    compute_kill_level,
)


# ---- kill switch ----


def test_kill_none_when_all_quiet():
    lvl, _ = compute_kill_level(rolling_drawdown=0.01, flash_move_pct=0.0, daily_pnl_pct=0.0)
    assert lvl == KillLevel.NONE


def test_kill_soft_on_small_dd():
    lvl, _ = compute_kill_level(rolling_drawdown=0.06, flash_move_pct=0.0, daily_pnl_pct=0.0)
    assert lvl == KillLevel.SOFT


def test_kill_hard_on_medium_dd():
    lvl, _ = compute_kill_level(rolling_drawdown=0.11, flash_move_pct=0.0, daily_pnl_pct=0.0)
    assert lvl == KillLevel.HARD


def test_kill_panic_on_flash_crash():
    lvl, reason = compute_kill_level(rolling_drawdown=0.0, flash_move_pct=0.06, daily_pnl_pct=0.0)
    assert lvl == KillLevel.PANIC
    assert "flash" in reason.lower()


def test_apply_kill_level_scales_target():
    assert apply_kill_level(1.0, KillLevel.NONE) == 1.0
    assert apply_kill_level(1.0, KillLevel.SOFT) == 0.5
    assert apply_kill_level(1.0, KillLevel.HARD) == 0.0
    assert apply_kill_level(1.0, KillLevel.PANIC) == 0.0


def test_blocks_new_gross_respects_tier():
    assert not blocks_new_gross(KillLevel.SOFT)
    assert blocks_new_gross(KillLevel.HARD)
    assert blocks_new_gross(KillLevel.PANIC)


# ---- concentration caps ----


def test_concentration_per_symbol_cap():
    targets = {"BTCUSDT": 0.4, "ETHUSDT": 0.1, "SOLUSDT": 0.05}
    out, rpt = apply_caps(targets, ConcentrationConfig(per_symbol=0.25, per_sector=1.0, total_gross=2.0))
    assert out["BTCUSDT"] <= 0.25 + 1e-9
    assert rpt.pre_gross > rpt.post_gross


def test_concentration_per_sector_cap():
    # Three altcoins together exceed per_sector 0.7
    targets = {"SOLUSDT": 0.25, "ADAUSDT": 0.25, "DOGEUSDT": 0.25}
    cfg = ConcentrationConfig(per_symbol=0.25, per_sector=0.3, total_gross=2.0)
    out, _ = apply_caps(targets, cfg)
    total_majors = sum(abs(v) for k, v in out.items())
    assert total_majors <= 0.3 + 1e-6


def test_concentration_total_gross_cap():
    targets = {"BTCUSDT": 0.2, "ETHUSDT": 0.2, "SOLUSDT": 0.2, "ADAUSDT": 0.2, "DOGEUSDT": 0.2}
    out, _ = apply_caps(targets, ConcentrationConfig(per_symbol=0.25, per_sector=1.0, total_gross=0.5))
    assert sum(abs(v) for v in out.values()) <= 0.5 + 1e-6


# ---- funding spike ----


def _history(rng_seed: int = 7) -> list[float]:
    import numpy as np
    rng = np.random.default_rng(rng_seed)
    return rng.normal(0.0001, 0.00005, 80).tolist()


def test_funding_spike_blocks_longs_on_positive_extreme():
    decision = check_funding_spike(
        recent_funding=_history(),
        current_funding=0.005,  # ~100× typical std
        config=FundingSpikeConfig(z_threshold=3.0),
    )
    assert decision.blocked
    assert decision.side == "long"


def test_funding_spike_passes_when_normal():
    decision = check_funding_spike(
        recent_funding=_history(),
        current_funding=0.00012,
        config=FundingSpikeConfig(z_threshold=3.0),
    )
    assert not decision.blocked


def test_basis_dislocation_blocks_both_sides():
    decision = check_funding_spike(
        recent_funding=_history(),
        current_funding=0.0001,
        spot_price=100.0,
        perp_price=101.0,
        config=FundingSpikeConfig(basis_threshold=0.005),
    )
    assert decision.blocked
    assert decision.side == "both"


def test_funding_insufficient_history_does_not_block():
    decision = check_funding_spike(recent_funding=[0.0001] * 5, current_funding=0.1)
    assert not decision.blocked
    assert decision.reason == "insufficient_history"


# ---- drift monitor ----


def test_drift_monitor_flags_recalibrate_after_persistent_divergence(tmp_path):
    m = DriftMonitor(
        DriftConfig(ewma_alpha=0.5, drift_ratio_kill=3.0, persistence_fills=3),
        log_path=tmp_path / "drift.jsonl",
    )
    for _ in range(6):
        m.record_fill(predicted_bps=5.0, actual_bps=20.0)
    assert m.stats.alert_level == "recalibrate"


def test_drift_monitor_stays_ok_when_aligned(tmp_path):
    m = DriftMonitor(log_path=tmp_path / "drift.jsonl")
    for _ in range(10):
        m.record_fill(predicted_bps=8.0, actual_bps=9.0)
    assert m.stats.alert_level == "ok"
