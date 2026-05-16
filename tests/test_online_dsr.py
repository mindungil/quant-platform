"""Tests for shared.statistics.online_dsr — rolling DSR + auto-pause state machine.

Lives at the repo root (NOT under tests/portfolio/) because online_dsr is
public (option-2 academic-baseline policy) — keeps this test in the
public quant-platform after split.
"""
from __future__ import annotations

import numpy as np
import pytest

from shared.statistics.online_dsr import (
    AlphaPauseDecider,
    OnlineDSR,
    rolling_dsr_from_history,
)


# ──────────────────────────────────────────────────────────────────
# OnlineDSR
# ──────────────────────────────────────────────────────────────────


def test_online_dsr_warmup_returns_none() -> None:
    """Before min_samples bars accumulated → None."""
    o = OnlineDSR(window_bars=100, min_samples=30)
    for _ in range(20):
        assert o.update(0.001) is None
    assert o.n_samples() == 20


def test_online_dsr_emits_after_warmup() -> None:
    """At min_samples returns a dict with the standard DSR fields."""
    o = OnlineDSR(window_bars=200, min_samples=50, periods_per_year=24 * 365)
    snapshot = None
    rng = np.random.default_rng(42)
    for _ in range(60):
        snapshot = o.update(float(rng.normal(0.0005, 0.01)))
    assert snapshot is not None
    assert {"sr_hat", "sr_benchmark", "dsr", "verdict"}.issubset(snapshot.keys())


def test_online_dsr_window_eviction() -> None:
    """Window holds at most window_bars; older returns get evicted."""
    o = OnlineDSR(window_bars=50, min_samples=30)
    for _ in range(100):
        o.update(0.001)
    assert o.n_samples() == 50


def test_online_dsr_window_below_min_samples_rejected() -> None:
    with pytest.raises(ValueError):
        OnlineDSR(window_bars=10, min_samples=30)


def test_online_dsr_strong_positive_edge_yields_high_dsr() -> None:
    """A clearly profitable stream → DSR near 1.0."""
    o = OnlineDSR(window_bars=500, min_samples=100, n_trials=1)
    rng = np.random.default_rng(0)
    rets = rng.normal(0.003, 0.005, 500)  # SR ≈ 0.6/bar, very high
    last = None
    for r in rets:
        last = o.update(float(r))
    assert last is not None
    assert last["dsr"] >= 0.9


def test_online_dsr_zero_edge_yields_low_dsr() -> None:
    """A break-even stream → DSR near 0 (not genuine)."""
    o = OnlineDSR(window_bars=500, min_samples=100, n_trials=20,
                  sr_std_across_trials=1.0)
    rng = np.random.default_rng(0)
    rets = rng.normal(0.0, 0.01, 500)  # zero edge
    last = None
    for r in rets:
        last = o.update(float(r))
    assert last is not None
    # With n_trials=20, even modest SRs from noise alone shouldn't beat
    # the expected-max bar — DSR should sit well below 0.9.
    assert last["dsr"] < 0.9


def test_rolling_dsr_from_history_lengths_match() -> None:
    rng = np.random.default_rng(1)
    rets = rng.normal(0.001, 0.01, 200).tolist()
    snaps = rolling_dsr_from_history(rets, window_bars=100, n_trials=1)
    assert len(snaps) == 200
    # First 29 → None (warmup), rest → dict
    assert all(s is None for s in snaps[:29])
    assert all(s is not None for s in snaps[30:])


# ──────────────────────────────────────────────────────────────────
# AlphaPauseDecider
# ──────────────────────────────────────────────────────────────────


def test_decider_single_bad_dsr_does_not_pause() -> None:
    d = AlphaPauseDecider(consecutive_required=3)
    state = d.step(0.2, "LIVE")
    assert state == "LIVE"
    assert "warning" in d.last_decision_reason


def test_decider_three_consecutive_bad_pauses() -> None:
    d = AlphaPauseDecider(pause_threshold=0.5, consecutive_required=3)
    state = "LIVE"
    state = d.step(0.2, state); assert state == "LIVE"
    state = d.step(0.2, state); assert state == "LIVE"
    state = d.step(0.2, state); assert state == "SHADOW"
    assert "paused_dsr" in d.last_decision_reason


def test_decider_recovery_requires_consecutive_good() -> None:
    d = AlphaPauseDecider(recover_threshold=0.7, consecutive_required=2)
    state = "SHADOW"
    state = d.step(0.8, state); assert state == "SHADOW"  # 1/2
    state = d.step(0.8, state); assert state == "LIVE"    # 2/2
    assert "promoted_dsr" in d.last_decision_reason


def test_decider_one_good_resets_bad_streak() -> None:
    """If we get bad, bad, then good — bad streak resets, no pause yet."""
    d = AlphaPauseDecider(consecutive_required=3)
    state = "LIVE"
    state = d.step(0.2, state)  # bad 1
    state = d.step(0.2, state)  # bad 2
    state = d.step(0.9, state)  # good → reset
    state = d.step(0.2, state)  # bad 1 (fresh)
    assert state == "LIVE"


def test_decider_none_dsr_is_no_op() -> None:
    """Warmup DSR=None → state unchanged, streaks reset."""
    d = AlphaPauseDecider(consecutive_required=2)
    state = d.step(0.2, "LIVE")  # bad 1
    state = d.step(None, state)
    assert state == "LIVE"
    assert d.last_decision_reason == "warmup_no_data"
    # Next bad should start a fresh streak
    state = d.step(0.2, state)
    assert state == "LIVE"  # only bad-1, not bad-2 yet


def test_decider_validates_thresholds() -> None:
    with pytest.raises(ValueError):
        AlphaPauseDecider(pause_threshold=0.8, recover_threshold=0.5)
    with pytest.raises(ValueError):
        AlphaPauseDecider(consecutive_required=0)


def test_decider_marginal_dsr_in_between_stays_in_current_state() -> None:
    """DSR between pause and recover thresholds: don't trigger either way."""
    d = AlphaPauseDecider(pause_threshold=0.5, recover_threshold=0.7,
                          consecutive_required=3)
    # LIVE side: DSR=0.6 (above pause) → stays LIVE, healthy
    assert d.step(0.6, "LIVE") == "LIVE"
    # SHADOW side: DSR=0.6 (below recover) → stays SHADOW
    assert d.step(0.6, "SHADOW") == "SHADOW"


def test_end_to_end_replay_pauses_then_recovers() -> None:
    """OnlineDSR + AlphaPauseDecider integration: a regime-shift in returns
    flips the decider's state appropriately."""
    rng = np.random.default_rng(7)
    # First 300 bars: profitable. Next 300: break-even. Then 300: profitable.
    good = rng.normal(0.003, 0.005, 300)
    bad = rng.normal(0.0, 0.01, 300)
    good2 = rng.normal(0.003, 0.005, 300)
    rets = np.concatenate([good, bad, good2])

    odsr = OnlineDSR(window_bars=200, min_samples=50, n_trials=5)
    decider = AlphaPauseDecider(pause_threshold=0.5, recover_threshold=0.7,
                                consecutive_required=2)
    state = "LIVE"
    state_history = []
    for r in rets:
        snap = odsr.update(float(r))
        dsr_val = snap["dsr"] if snap else None
        state = decider.step(dsr_val, state)
        state_history.append(state)

    # During good phase: mostly LIVE
    assert state_history[200] == "LIVE"
    # By end of bad phase: should have flipped to SHADOW at least once
    assert "SHADOW" in state_history[300:600]
    # By end of recovery phase: should be back to LIVE
    assert state_history[-1] == "LIVE"
