"""Tests for shared.execution.maker_taker_bandit — V3 #3.

Lives at repo root (public-ish file — execution-side helper). The
bandit itself is plumbing on top of FormulaMAB-style stats.
"""
from __future__ import annotations

import random

import pytest

from shared.execution.maker_taker_bandit import (
    MakerTakerBandit,
    context_key,
    slippage_to_reward,
)


# ──────────────────────────────────────────────────────────────────
# context_key — bucketing
# ──────────────────────────────────────────────────────────────────


def test_context_key_includes_all_dims() -> None:
    k = context_key(spread_bp=3.0, annualized_vol=0.10, order_size_usd=500,
                    urgency="low")
    assert k.startswith("s0_v0_z0_u")
    assert k.endswith("low")


def test_context_key_buckets_at_edges() -> None:
    # spread 4.999bp → bucket 0 (< 5); 5.0bp → bucket 1
    k1 = context_key(spread_bp=4.99, annualized_vol=0.10, order_size_usd=100, urgency="low")
    k2 = context_key(spread_bp=5.0, annualized_vol=0.10, order_size_usd=100, urgency="low")
    assert k1.split("_")[0] == "s0"
    assert k2.split("_")[0] == "s1"


def test_context_key_caps_urgency_to_known() -> None:
    k = context_key(spread_bp=10, annualized_vol=0.3, order_size_usd=1000, urgency="weird")
    assert k.endswith("normal")  # falls back to 'normal'


def test_context_key_negative_inputs_clamp_to_zero() -> None:
    k = context_key(spread_bp=-5, annualized_vol=-0.1, order_size_usd=-100, urgency="low")
    assert "s0" in k and "v0" in k and "z0" in k


# ──────────────────────────────────────────────────────────────────
# slippage_to_reward
# ──────────────────────────────────────────────────────────────────


def test_slippage_to_reward_sign() -> None:
    """Adverse slippage → negative reward."""
    assert slippage_to_reward(20.0) == pytest.approx(-0.20)
    assert slippage_to_reward(-15.0) == pytest.approx(0.15)
    assert slippage_to_reward(0.0) == 0.0


# ──────────────────────────────────────────────────────────────────
# Bandit lifecycle
# ──────────────────────────────────────────────────────────────────


def test_select_returns_known_action() -> None:
    random.seed(0)
    b = MakerTakerBandit(epsilon=0.0)  # disable forced exploration for determinism
    a = b.select("test_ctx")
    assert a in ("MAKER", "TAKER")


def test_update_records_observations() -> None:
    b = MakerTakerBandit(epsilon=0.0)
    b.update("ctx1", "MAKER", -0.05)
    b.update("ctx1", "MAKER", -0.03)
    b.update("ctx1", "TAKER", -0.15)
    assert b.n_observations("ctx1") == 3


def test_update_rejects_bad_action() -> None:
    b = MakerTakerBandit()
    with pytest.raises(ValueError):
        b.update("ctx", "FOO", 0.0)  # type: ignore[arg-type]


def test_best_arm_returns_higher_posterior() -> None:
    """After many maker-positive observations, best_arm should be MAKER."""
    random.seed(42)
    b = MakerTakerBandit(epsilon=0.0)
    for _ in range(50):
        b.update("ctx", "MAKER", -0.02)   # small adverse cost
        b.update("ctx", "TAKER", -0.20)   # 10x worse
    assert b.best_arm("ctx") == "MAKER"


def test_thompson_converges_to_better_arm() -> None:
    """With epsilon=0 and many updates, the loser arm should rarely win."""
    random.seed(42)
    b = MakerTakerBandit(epsilon=0.0)
    # Seed posteriors
    for _ in range(80):
        b.update("ctx", "MAKER", -0.02)
        b.update("ctx", "TAKER", -0.30)

    maker_wins = sum(b.select("ctx") == "MAKER" for _ in range(500))
    assert maker_wins > 400, f"expected MAKER to win most of 500 draws, got {maker_wins}"


def test_epsilon_floor_forces_exploration() -> None:
    """With epsilon=1.0 (always explore), MAKER should win ~50% even when
    TAKER has much better posterior."""
    random.seed(123)
    b = MakerTakerBandit(epsilon=1.0)
    for _ in range(30):
        b.update("ctx", "TAKER", 0.5)  # taker far better
        b.update("ctx", "MAKER", -0.5)

    maker_picks = sum(b.select("ctx") == "MAKER" for _ in range(400))
    # epsilon=1.0 → uniformly random → ~50%
    assert 150 < maker_picks < 250


# ──────────────────────────────────────────────────────────────────
# Stats / inspection
# ──────────────────────────────────────────────────────────────────


def test_get_stats_shape() -> None:
    b = MakerTakerBandit()
    b.update("ctx", "MAKER", -0.01)
    b.update("ctx", "TAKER", -0.10)
    stats = b.get_stats()
    assert "ctx" in stats
    assert set(stats["ctx"].keys()) == {"MAKER", "TAKER"}
    assert stats["ctx"]["MAKER"]["n"] == 1
    assert stats["ctx"]["TAKER"]["n"] == 1


def test_n_observations_unknown_ctx_returns_zero() -> None:
    b = MakerTakerBandit()
    assert b.n_observations("never_seen_ctx") == 0


# ──────────────────────────────────────────────────────────────────
# Serialize round-trip
# ──────────────────────────────────────────────────────────────────


def test_serialize_round_trip_preserves_arms() -> None:
    b = MakerTakerBandit(epsilon=0.20)
    for _ in range(15):
        b.update("ctx_a", "MAKER", -0.04)
        b.update("ctx_b", "TAKER", -0.18)
    snap = b.serialize()
    b2 = MakerTakerBandit.deserialize(snap)
    assert b2.epsilon == 0.20
    assert b2.n_observations("ctx_a") == 15
    assert b2.n_observations("ctx_b") == 15
    # Stats should match (within float tolerance)
    s1 = b.get_stats()
    s2 = b2.get_stats()
    for ctx in s1:
        for a in s1[ctx]:
            assert s1[ctx][a]["n"] == s2[ctx][a]["n"]
            assert s1[ctx][a]["mean_reward"] == pytest.approx(s2[ctx][a]["mean_reward"])


def test_serialize_round_trip_empty() -> None:
    b = MakerTakerBandit()
    snap = b.serialize()
    b2 = MakerTakerBandit.deserialize(snap)
    assert b2.get_stats() == {}


# ──────────────────────────────────────────────────────────────────
# Realistic scenario — wide spread → maker preferred
# ──────────────────────────────────────────────────────────────────


def test_per_context_separation() -> None:
    """Wide-spread ctx and tight-spread ctx should learn different best arms."""
    random.seed(7)
    b = MakerTakerBandit(epsilon=0.0)
    wide_ctx = context_key(spread_bp=30, annualized_vol=0.4,
                           order_size_usd=2000, urgency="normal")
    tight_ctx = context_key(spread_bp=2, annualized_vol=0.2,
                            order_size_usd=2000, urgency="high")

    # In wide-spread: MAKER usually fills well (low slippage); TAKER pays the spread.
    for _ in range(40):
        b.update(wide_ctx, "MAKER", -0.03)
        b.update(wide_ctx, "TAKER", -0.30)
    # In tight-spread + high urgency: TAKER is better (no fill risk).
    for _ in range(40):
        b.update(tight_ctx, "MAKER", -0.10)   # adverse-selection cost
        b.update(tight_ctx, "TAKER", -0.02)

    assert b.best_arm(wide_ctx) == "MAKER"
    assert b.best_arm(tight_ctx) == "TAKER"
