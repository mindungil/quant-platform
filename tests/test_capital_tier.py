"""Tests for shared.risk.capital_tier — V4-2."""
from __future__ import annotations

import os

import pytest

from shared.risk import capital_tier
from shared.risk.capital_tier import (
    TierStats,
    cap_order_notional,
    clear_kill,
    current_spec,
    current_tier,
    evaluate_tier_transition,
    max_daily_notional,
    max_order_notional,
    next_tier,
    prev_tier,
    register_kill_from_risk_hub,
    set_active_tier,
    should_demote,
    should_promote,
    snapshot,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset module-level state between tests."""
    os.environ.pop("CAPITAL_TIER", None)
    set_active_tier("PAPER", reason="test_reset")
    clear_kill()
    yield
    os.environ.pop("CAPITAL_TIER", None)
    set_active_tier("PAPER", reason="test_reset")
    clear_kill()


# ─── Active tier basics ─────────────────────────────────────────────


def test_default_tier_is_paper() -> None:
    assert current_tier() == "PAPER"
    assert max_order_notional() == pytest.approx(0.01)


def test_set_active_tier_changes_tier() -> None:
    set_active_tier("SMALL")
    assert current_tier() == "SMALL"
    assert max_order_notional() == pytest.approx(100.0)


def test_set_active_tier_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        set_active_tier("WAT")  # type: ignore[arg-type]


# ─── Env override ───────────────────────────────────────────────────


def test_env_override_wins_over_runtime() -> None:
    set_active_tier("MID")
    os.environ["CAPITAL_TIER"] = "MICRO"
    assert current_tier() == "MICRO"
    assert max_order_notional() == pytest.approx(10.0)


def test_env_override_garbage_is_ignored() -> None:
    set_active_tier("SMALL")
    os.environ["CAPITAL_TIER"] = "garbage_value"
    assert current_tier() == "SMALL"


def test_env_override_is_case_insensitive() -> None:
    os.environ["CAPITAL_TIER"] = "mid"
    assert current_tier() == "MID"


# ─── HARD-kill forcing ──────────────────────────────────────────────


def test_hard_kill_forces_paper() -> None:
    set_active_tier("FULL")
    register_kill_from_risk_hub()
    assert current_tier() == "PAPER"
    assert max_order_notional() == pytest.approx(0.01)


def test_clear_kill_restores_runtime_tier() -> None:
    set_active_tier("MID")
    register_kill_from_risk_hub()
    assert current_tier() == "PAPER"
    clear_kill()
    assert current_tier() == "MID"


def test_env_override_beats_kill() -> None:
    """Operator pinning the tier overrides even the kill signal."""
    register_kill_from_risk_hub()
    os.environ["CAPITAL_TIER"] = "MICRO"
    assert current_tier() == "MICRO"


# ─── cap_order_notional ─────────────────────────────────────────────


def test_cap_order_notional_clips_to_tier_max() -> None:
    set_active_tier("MICRO")  # max = $10
    assert cap_order_notional(50.0) == pytest.approx(10.0)
    assert cap_order_notional(5.0) == pytest.approx(5.0)


def test_cap_order_notional_floors_at_zero() -> None:
    set_active_tier("SMALL")
    assert cap_order_notional(-10.0) == 0.0


# ─── Promotion logic ────────────────────────────────────────────────


def test_promote_when_all_criteria_met() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=80, realized_sharpe=1.5, realized_max_dd=0.02, hard_kill_events=0)
    assert should_promote(stats)


def test_no_promote_with_too_few_trades() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=10, realized_sharpe=2.0, realized_max_dd=0.01)
    assert not should_promote(stats)


def test_no_promote_with_low_sharpe() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=100, realized_sharpe=0.5, realized_max_dd=0.01)
    assert not should_promote(stats)


def test_no_promote_with_high_dd() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=100, realized_sharpe=1.5, realized_max_dd=0.10)
    assert not should_promote(stats)


def test_no_promote_with_kill_event() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=100, realized_sharpe=2.0, realized_max_dd=0.01, hard_kill_events=1)
    assert not should_promote(stats)


def test_no_promote_at_top_tier() -> None:
    set_active_tier("FULL")
    stats = TierStats(n_trades=1000, realized_sharpe=3.0, realized_max_dd=0.01)
    assert not should_promote(stats)


# ─── Demotion logic ─────────────────────────────────────────────────


def test_demote_on_kill_event() -> None:
    set_active_tier("SMALL")
    stats = TierStats(n_trades=20, realized_sharpe=0.5, realized_max_dd=0.02, hard_kill_events=1)
    assert should_demote(stats)


def test_demote_on_negative_sharpe() -> None:
    set_active_tier("SMALL")
    stats = TierStats(n_trades=100, realized_sharpe=-0.7, realized_max_dd=0.03)
    assert should_demote(stats)


def test_demote_on_high_dd() -> None:
    set_active_tier("SMALL")
    stats = TierStats(n_trades=100, realized_sharpe=0.5, realized_max_dd=0.15)
    assert should_demote(stats)


def test_no_demote_at_bottom_tier() -> None:
    set_active_tier("PAPER")
    stats = TierStats(n_trades=10, realized_sharpe=-2.0, realized_max_dd=0.5, hard_kill_events=5)
    assert not should_demote(stats)


# ─── Tier walking ──────────────────────────────────────────────────


def test_next_tier() -> None:
    assert next_tier("PAPER") == "MICRO"
    assert next_tier("MICRO") == "SMALL"
    assert next_tier("FULL") is None


def test_prev_tier() -> None:
    assert prev_tier("FULL") == "MID"
    assert prev_tier("MICRO") == "PAPER"
    assert prev_tier("PAPER") is None


# ─── evaluate_tier_transition ──────────────────────────────────────


def test_transition_promotes() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=80, realized_sharpe=1.5, realized_max_dd=0.02)
    assert evaluate_tier_transition(stats) == "SMALL"


def test_transition_demotes() -> None:
    set_active_tier("SMALL")
    stats = TierStats(n_trades=100, realized_sharpe=-0.8, realized_max_dd=0.05)
    assert evaluate_tier_transition(stats) == "MICRO"


def test_transition_no_change() -> None:
    set_active_tier("MICRO")
    stats = TierStats(n_trades=20, realized_sharpe=0.5, realized_max_dd=0.03)
    assert evaluate_tier_transition(stats) is None


def test_demotion_wins_over_promotion_on_conflict() -> None:
    """Defensive: if both criteria fire (rare), demote."""
    set_active_tier("MICRO")
    # Construct a hard-kill that satisfies promote but should still demote
    stats = TierStats(n_trades=100, realized_sharpe=1.5,
                      realized_max_dd=0.02, hard_kill_events=1)
    assert evaluate_tier_transition(stats) == "PAPER"  # demoted from MICRO


# ─── Snapshot ──────────────────────────────────────────────────────


def test_snapshot_shape() -> None:
    set_active_tier("SMALL")
    snap = snapshot()
    assert snap["active_tier"] == "SMALL"
    assert snap["max_order_notional"] == pytest.approx(100.0)
    assert "tiers" in snap
    assert len(snap["tiers"]) == 5
