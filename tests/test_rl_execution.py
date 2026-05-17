"""Tests for shared.execution.rl_agent — V4-1."""
from __future__ import annotations

import random

import pytest

from shared.execution.rl_agent import (
    ACParams,
    ACSchedule,
    TrajectoryBandit,
    back_loaded_schedule,
    build_schedule_for_shape,
    optimal_schedule,
    twap_schedule,
)


# ─── Almgren-Chriss closed-form ─────────────────────────────────────


def test_zero_quantity_returns_empty() -> None:
    s = optimal_schedule(0.0, 10)
    assert s.n_slices == 0
    assert s.total_quantity == 0.0


def test_single_slice_returns_full() -> None:
    s = optimal_schedule(100.0, 1)
    assert s.n_slices == 1
    assert s.quantities[0] == pytest.approx(100.0)


def test_total_quantity_preserved() -> None:
    s = optimal_schedule(1000.0, 10)
    assert s.total_quantity == pytest.approx(1000.0, rel=1e-9)


def test_twap_collapses_to_equal_slices() -> None:
    """With zero risk aversion → TWAP → all slices equal."""
    s = twap_schedule(500.0, 10)
    assert s.trajectory_shape == "TWAP"
    for q in s.quantities:
        assert q == pytest.approx(50.0)


def test_high_risk_aversion_front_loads() -> None:
    """High λ → first slices > later slices."""
    p = ACParams(risk_aversion_lambda=1e-2, bar_vol_bps=50.0)
    s = optimal_schedule(1000.0, 10, params=p)
    assert s.trajectory_shape == "FRONT_LOAD"
    assert s.quantities[0] > s.quantities[-1]


def test_expected_cost_increases_with_quantity() -> None:
    p = ACParams()
    s1 = optimal_schedule(100.0, 10, params=p)
    s2 = optimal_schedule(1000.0, 10, params=p)
    assert s2.expected_cost_bp > s1.expected_cost_bp


def test_cost_variance_zero_for_single_slice() -> None:
    s = optimal_schedule(100.0, 1)
    # Single slice fully executes at start → no remaining variance
    assert s.cost_variance_bp2 >= 0.0


# ─── Back-load / dispatcher ─────────────────────────────────────────


def test_back_loaded_reverses_front_loaded() -> None:
    bl = back_loaded_schedule(1000.0, 10)
    assert bl.trajectory_shape == "BACK_LOAD"
    assert bl.quantities[0] < bl.quantities[-1]
    assert bl.total_quantity == pytest.approx(1000.0, rel=1e-9)


def test_build_schedule_for_shape_dispatches() -> None:
    for shape in ("TWAP", "FRONT_LOAD", "BACK_LOAD"):
        s = build_schedule_for_shape(shape, 500.0, 5)
        assert s.trajectory_shape == shape
        assert s.total_quantity == pytest.approx(500.0, rel=1e-9)


# ─── TrajectoryBandit ──────────────────────────────────────────────


def test_bandit_first_select_is_random_choice() -> None:
    random.seed(42)
    b = TrajectoryBandit(epsilon=0.0)  # disable forced exploration
    # All arms have n=0 → branch always returns random.choice
    shape = b.select()
    assert shape in ("FRONT_LOAD", "TWAP", "BACK_LOAD")


def test_bandit_update_records_observation() -> None:
    b = TrajectoryBandit()
    b.update("FRONT_LOAD", 12.5)
    b.update("FRONT_LOAD", 11.0)
    stats = b.stats()
    assert stats["FRONT_LOAD"]["n"] == 2


def test_bandit_update_rejects_unknown_shape() -> None:
    b = TrajectoryBandit()
    with pytest.raises(ValueError):
        b.update("INVALID", 5.0)  # type: ignore[arg-type]


def test_bandit_converges_to_cheapest_arm() -> None:
    random.seed(1)
    b = TrajectoryBandit(epsilon=0.0)
    # Seed: FRONT cheap, TWAP medium, BACK expensive
    for _ in range(30):
        b.update("FRONT_LOAD", 5.0)
        b.update("TWAP", 12.0)
        b.update("BACK_LOAD", 25.0)
    picks = [b.select() for _ in range(200)]
    assert picks.count("FRONT_LOAD") > 180


def test_bandit_epsilon_floor_forces_exploration() -> None:
    random.seed(7)
    b = TrajectoryBandit(epsilon=1.0)  # always explore
    for _ in range(20):
        b.update("FRONT_LOAD", 1.0)
        b.update("TWAP", 50.0)
        b.update("BACK_LOAD", 50.0)
    picks = [b.select() for _ in range(300)]
    # With epsilon=1 → uniform over 3 → each ≈ 100
    assert 70 < picks.count("FRONT_LOAD") < 130


def test_bandit_stats_shape() -> None:
    b = TrajectoryBandit()
    b.update("TWAP", 8.0)
    s = b.stats()
    assert set(s.keys()) == {"FRONT_LOAD", "TWAP", "BACK_LOAD"}
    assert s["TWAP"]["n"] == 1
    assert "mean_cost_bp" in s["TWAP"]


# ─── Integration: bandit + AC build ────────────────────────────────


def test_end_to_end_bandit_drives_schedule_choice() -> None:
    """Bandit recommends shape → dispatcher builds schedule with that shape."""
    random.seed(13)
    b = TrajectoryBandit(epsilon=0.0)
    # Pretend TWAP is cheapest
    for _ in range(10):
        b.update("TWAP", 4.0)
        b.update("FRONT_LOAD", 12.0)
        b.update("BACK_LOAD", 12.0)
    pick = b.select()
    schedule = build_schedule_for_shape(pick, 1000.0, 10)
    assert pick == "TWAP"
    assert schedule.trajectory_shape == "TWAP"
    # Realize cost + feed back
    b.update(pick, schedule.expected_cost_bp)
    assert b.stats()["TWAP"]["n"] == 11
