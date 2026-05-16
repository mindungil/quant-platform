"""Tests for shared.learning.LearningLoop — the V2-modules closed-loop.

Lives at repo root (NOT under tests/portfolio/ or tests/alpha/) because
the module is V3 IP but stitches together V2 public + IP pieces; the
underlying state-machine semantics deserve full coverage.

Covers:
- Per-alpha DSR/decider lifecycle (warmup → emit → flip state)
- Factor decay → active_weight goes 1 → 0
- Warm start (round-trip through InMemoryStateStore) preserves state
- Bulk update helpers
- get_alphas_by_state / get_decayed_factors accessors
- State change detection (state_changed flag is correct)
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from shared.learning import (
    AlphaLoopResult,
    FactorLoopResult,
    LearningLoop,
    LearningLoopConfig,
)
from shared.learning.loop import InMemoryStateStore


# ──────────────────────────────────────────────────────────────────
# Smoke / lifecycle
# ──────────────────────────────────────────────────────────────────


def test_first_pnl_creates_alpha_state() -> None:
    loop = LearningLoop()
    result = loop.update_alpha_pnl("alpha_a", 0.001)
    assert result.prev_state == "LIVE"
    assert result.new_state == "LIVE"
    assert not result.state_changed
    # Decider hasn't seen enough data → warmup_no_data reason
    assert result.dsr is None


def test_alpha_can_flip_live_to_shadow_on_bad_streak() -> None:
    cfg = LearningLoopConfig(
        dsr_window_bars=100,
        dsr_min_samples=50,
        dsr_n_trials=20,
        pause_threshold=0.5,
        consecutive_required=2,
    )
    loop = LearningLoop(config=cfg)
    rng = np.random.default_rng(0)

    # Phase 1: 200 bars of zero-edge noise. With n_trials=20 the DSR
    # benchmark is high, so DSR will sit below 0.5.
    last_state = "LIVE"
    state_changed_any = False
    for _ in range(200):
        r = loop.update_alpha_pnl("alpha_x", float(rng.normal(0, 0.01)))
        if r.state_changed:
            state_changed_any = True
        last_state = r.new_state
    assert state_changed_any, "expected at least one LIVE→SHADOW flip in 200 noise bars"
    assert last_state == "SHADOW"


def test_alpha_can_recover_shadow_to_live() -> None:
    cfg = LearningLoopConfig(
        dsr_window_bars=200,
        dsr_min_samples=50,
        dsr_n_trials=5,
        pause_threshold=0.5,
        recover_threshold=0.7,
        consecutive_required=2,
    )
    loop = LearningLoop(config=cfg)
    rng = np.random.default_rng(7)

    # Force it into SHADOW first
    for _ in range(200):
        loop.update_alpha_pnl("a", float(rng.normal(0.0, 0.01)))
    # By construction, decider may or may not have flipped — explicitly
    # force the state to SHADOW so we test the recovery path
    loop._alpha_state["a"] = "SHADOW"
    loop._alpha_decider["a"].reset_streaks()

    # Now feed strong positive edge
    last = None
    for _ in range(400):
        last = loop.update_alpha_pnl("a", float(rng.normal(0.003, 0.005)))
    assert last is not None
    assert last.new_state == "LIVE"


# ──────────────────────────────────────────────────────────────────
# Factor decay
# ──────────────────────────────────────────────────────────────────


def test_factor_active_weight_drops_on_noise() -> None:
    cfg = LearningLoopConfig(
        factor_ic_window=20,
        factor_ir_window=30,
        factor_ir_threshold=0.5,
    )
    loop = LearningLoop(config=cfg)
    rng = np.random.default_rng(1)
    # 1000 zero-correlation pairs → IC_IR ~ 0 → decayed
    last = None
    for _ in range(1000):
        last = loop.update_factor_ic("f_noise", float(rng.normal()), float(rng.normal()))
    assert last is not None
    assert last.is_decayed
    assert last.new_active_weight == 0.0


def test_factor_weight_change_flagged() -> None:
    cfg = LearningLoopConfig(
        factor_ic_window=20,
        factor_ir_window=30,
        factor_ir_threshold=0.5,
    )
    loop = LearningLoop(config=cfg)
    rng = np.random.default_rng(1)
    # Most updates → weight stays at 1.0 (warmup)
    flip_count = 0
    for _ in range(2000):
        s = float(rng.normal())
        r = float(rng.normal())
        out = loop.update_factor_ic("f", s, r)
        if out.weight_changed:
            flip_count += 1
    # Should flip at least once (1→0 when first declared decayed)
    assert flip_count >= 1


# ──────────────────────────────────────────────────────────────────
# Warm start (Redis-style round trip)
# ──────────────────────────────────────────────────────────────────


def test_warm_start_round_trip() -> None:
    store = InMemoryStateStore()
    cfg = LearningLoopConfig(dsr_window_bars=200, dsr_min_samples=30)

    # Cycle 1: populate
    loop1 = LearningLoop(config=cfg, state_store=store)
    rng = np.random.default_rng(3)
    for _ in range(100):
        loop1.update_alpha_pnl("alpha_a", float(rng.normal(0.001, 0.005)))
    for _ in range(80):
        loop1.update_factor_ic("f1", float(rng.normal()), float(rng.normal()))
    written = loop1.checkpoint()
    assert written > 0

    snap1_alphas = loop1.snapshot_alphas()
    snap1_factors = loop1.snapshot_factors()
    assert "alpha_a" in snap1_alphas
    assert "f1" in snap1_factors

    # Cycle 2: fresh instance, warm-start from the store
    loop2 = LearningLoop(config=cfg, state_store=store)
    loop2.warm_start()
    snap2_alphas = loop2.snapshot_alphas()
    snap2_factors = loop2.snapshot_factors()

    assert "alpha_a" in snap2_alphas
    assert snap2_alphas["alpha_a"]["n_samples"] == snap1_alphas["alpha_a"]["n_samples"]
    assert snap2_alphas["alpha_a"]["state"] == snap1_alphas["alpha_a"]["state"]
    assert "f1" in snap2_factors
    assert snap2_factors["f1"]["n_obs"] == snap1_factors["f1"]["n_obs"]


def test_warm_start_no_op_when_store_empty() -> None:
    store = InMemoryStateStore()
    loop = LearningLoop(state_store=store)
    loop.warm_start()  # should not raise
    assert loop.snapshot_alphas() == {}


def test_warm_start_no_store_is_silent() -> None:
    loop = LearningLoop()  # no state_store
    loop.warm_start()
    assert loop.checkpoint() == 0


# ──────────────────────────────────────────────────────────────────
# Accessors
# ──────────────────────────────────────────────────────────────────


def test_get_alphas_by_state_partitions_correctly() -> None:
    loop = LearningLoop()
    loop.update_alpha_pnl("live_a", 0.001)
    loop.update_alpha_pnl("live_b", 0.001)
    # Manually flip one to SHADOW for the test
    loop._alpha_state["live_b"] = "SHADOW"
    live = loop.get_alphas_by_state("LIVE")
    shadow = loop.get_alphas_by_state("SHADOW")
    assert "live_a" in live and "live_b" not in live
    assert "live_b" in shadow and "live_a" not in shadow


def test_get_decayed_factors_returns_list() -> None:
    cfg = LearningLoopConfig(
        factor_ic_window=15,
        factor_ir_window=20,
        factor_ir_threshold=0.5,
    )
    loop = LearningLoop(config=cfg)
    rng = np.random.default_rng(4)
    # Force a factor to decay
    for _ in range(800):
        loop.update_factor_ic("f_dead", float(rng.normal()), float(rng.normal()))
    # And a fresh factor (still warming up)
    for _ in range(10):
        loop.update_factor_ic("f_fresh", float(rng.normal()), float(rng.normal()))
    decayed = loop.get_decayed_factors()
    assert "f_dead" in decayed
    assert "f_fresh" not in decayed


# ──────────────────────────────────────────────────────────────────
# Bulk update
# ──────────────────────────────────────────────────────────────────


def test_update_alpha_pnl_bulk_returns_one_per_alpha() -> None:
    loop = LearningLoop()
    out = loop.update_alpha_pnl_bulk([
        ("a", 0.001),
        ("b", -0.0005),
        ("c", 0.002),
    ])
    assert len(out) == 3
    names = {r.alpha_name for r in out}
    assert names == {"a", "b", "c"}


def test_update_factor_ic_bulk_returns_one_per_factor() -> None:
    loop = LearningLoop()
    out = loop.update_factor_ic_bulk([
        ("f1", 0.3, 0.01),
        ("f2", -0.2, -0.01),
    ])
    assert len(out) == 2
    assert {r.factor_name for r in out} == {"f1", "f2"}


# ──────────────────────────────────────────────────────────────────
# Event payload
# ──────────────────────────────────────────────────────────────────


def test_alpha_loop_result_as_event_dict_shape() -> None:
    loop = LearningLoop()
    r = loop.update_alpha_pnl("alpha_z", 0.0005)
    event = r.as_event()
    assert event["alpha_name"] == "alpha_z"
    assert set(event.keys()) >= {
        "alpha_name", "prev_state", "new_state",
        "state_changed", "dsr", "decision_reason",
    }
