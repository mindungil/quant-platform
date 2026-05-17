"""Tests for shared.execution.multi_venue_arb — V4-6."""
from __future__ import annotations

import pytest

from shared.execution.maker_taker_bandit import MakerTakerBandit
from shared.execution.multi_venue_arb import (
    ArbPairOrder,
    execute_arb_pair,
)
from shared.execution.router import VenueQuote
from shared.risk import capital_tier
from shared.risk.monitor_hub import (
    RiskEvent,
    clear_kill,
    clear_notifiers,
    clear_throttle,
    emit,
)


@pytest.fixture(autouse=True)
def _reset():
    capital_tier.set_active_tier("FULL", reason="test")
    capital_tier.clear_kill()
    clear_kill("global")
    clear_kill("multi-venue-arb")
    clear_throttle("global")
    clear_throttle("multi-venue-arb")
    clear_notifiers()
    yield
    capital_tier.set_active_tier("PAPER", reason="test_reset")
    clear_kill("global")
    clear_kill("multi-venue-arb")
    clear_throttle("global")
    clear_throttle("multi-venue-arb")


def _vq(venue: str, bid: float, ask: float, depth: float = 100) -> VenueQuote:
    return VenueQuote(venue, bid, ask, depth, depth)


def _pair_order(
    qty: float = 1.0,
    long_symbol: str = "BTC_SPOT",
    short_symbol: str = "BTC_PERP",
) -> ArbPairOrder:
    return ArbPairOrder(
        long_symbol=long_symbol,
        short_symbol=short_symbol,
        requested_quantity=qty,
        target_notional_usd=qty * 50_000,
        long_venue_quotes={
            "binance": _vq("binance", 49_900, 50_100),
            "coinbase": _vq("coinbase", 49_950, 50_050),  # cheapest ask
        },
        short_venue_quotes={
            "binance": _vq("binance", 50_010, 50_120),  # highest bid
            "okx": _vq("okx", 49_980, 50_100),
        },
    )


# ─── Risk hub gates ────────────────────────────────────────────────


def test_hard_kill_blocks_execution() -> None:
    emit(RiskEvent(event_class="HARD", reason="dd_30", scope="global"))
    out = execute_arb_pair(_pair_order())
    assert out.skipped_reason == "risk_hub_hard_kill"
    assert out.executed_quantity == 0.0


def test_scope_specific_kill_blocks() -> None:
    emit(RiskEvent(event_class="HARD", reason="arb_specific", scope="multi-venue-arb"))
    out = execute_arb_pair(_pair_order())
    assert out.skipped_reason == "risk_hub_hard_kill"


def test_soft_throttle_scales_quantity() -> None:
    emit(RiskEvent(event_class="SOFT", reason="vol_spike",
                    scope="multi-venue-arb", multiplier=0.4))
    out = execute_arb_pair(_pair_order(qty=10.0))
    # executed_quantity should be ≤ 10 * 0.4 = 4.0
    assert out.executed_quantity <= 4.0 + 1e-9


def test_soft_throttle_zero_skips() -> None:
    emit(RiskEvent(event_class="SOFT", reason="full_throttle",
                    scope="multi-venue-arb", multiplier=0.0))
    out = execute_arb_pair(_pair_order())
    assert out.skipped_reason == "soft_throttle_zero"


# ─── Capital tier ──────────────────────────────────────────────────


def test_tier_caps_oversize_notional() -> None:
    capital_tier.set_active_tier("MICRO")  # max $10
    out = execute_arb_pair(_pair_order(qty=1.0))  # ~$50k notional
    # Should be capped — long_plan total_quantity heavily reduced
    assert out.notional_capped
    # 10 / ~50_075 ≈ 0.0002 BTC executed
    assert out.executed_quantity < 0.001


def test_tier_field_in_result() -> None:
    capital_tier.set_active_tier("SMALL")
    out = execute_arb_pair(_pair_order())
    assert out.capital_tier == "SMALL"


# ─── Venue routing — leg correctness ───────────────────────────────


def test_long_leg_picks_cheapest_ask() -> None:
    out = execute_arb_pair(_pair_order())
    # cheapest ask = coinbase (50050) → first fill from coinbase
    assert out.long_plan is not None
    assert out.long_plan.fills[0].venue == "coinbase"


def test_short_leg_picks_highest_bid() -> None:
    out = execute_arb_pair(_pair_order())
    # highest bid = binance (50010) → first fill from binance
    assert out.short_plan is not None
    assert out.short_plan.fills[0].venue == "binance"


def test_executed_quantity_is_min_of_both_legs() -> None:
    out = execute_arb_pair(_pair_order(qty=2.0))
    assert out.executed_quantity == pytest.approx(
        min(out.long_plan.total_quantity, out.short_plan.total_quantity)
    )


# ─── Bandit integration ───────────────────────────────────────────


def test_bandit_called_per_leg() -> None:
    bandit = MakerTakerBandit(epsilon=0.0)
    # Force TAKER on tight, MAKER on wide — seed posteriors
    for _ in range(20):
        bandit.update("s0_v1_z2_unormal", "TAKER", -0.01)
        bandit.update("s0_v1_z2_unormal", "MAKER", -0.10)
    out = execute_arb_pair(_pair_order(), bandit=bandit)
    assert out.long_action in ("MAKER", "TAKER")
    assert out.short_action in ("MAKER", "TAKER")


def test_no_bandit_defaults_to_taker() -> None:
    out = execute_arb_pair(_pair_order(), bandit=None)
    assert out.long_action == "TAKER"
    assert out.short_action == "TAKER"


# ─── Summary shape ────────────────────────────────────────────────


def test_summary_includes_expected_keys() -> None:
    out = execute_arb_pair(_pair_order())
    s = out.summary()
    assert {"executed_quantity", "capital_tier", "long_action", "short_action",
            "long_legs", "short_legs"} <= s.keys()


def test_partial_fill_emits_obs_event() -> None:
    received: list[RiskEvent] = []
    from shared.risk.monitor_hub import register_notifier
    register_notifier(received.append)
    # Tight depth at both venues so tier-capped qty still exceeds depth
    order = ArbPairOrder(
        long_symbol="BTC_SPOT",
        short_symbol="BTC_PERP",
        requested_quantity=1.0,
        target_notional_usd=50_000,
        long_venue_quotes={
            "binance": _vq("binance", 49_900, 50_100, depth=0.001),
            "coinbase": _vq("coinbase", 49_950, 50_050, depth=0.001),
        },
        short_venue_quotes={
            "binance": _vq("binance", 50_010, 50_120, depth=0.001),
            "okx": _vq("okx", 49_980, 50_100, depth=0.001),
        },
    )
    out = execute_arb_pair(order)
    arb_obs = [e for e in received if e.reason == "arb_partial_fill_legs"]
    assert len(arb_obs) >= 1
