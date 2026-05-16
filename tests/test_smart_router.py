"""Tests for shared.execution.router — V3 #6 Smart Order Routing.

IP test (router controls real venue traffic + fee handling).
"""
from __future__ import annotations

import pytest

from shared.execution.router import (
    FeeSchedule,
    RouterFill,
    RoutingPlan,
    VenueQuote,
    route_order,
    savings_vs_single_venue,
)


def _q(
    venue: str,
    bid: float,
    ask: float,
    bid_depth: float = 10.0,
    ask_depth: float = 10.0,
) -> VenueQuote:
    return VenueQuote(venue=venue, best_bid=bid, best_ask=ask,
                      bid_depth=bid_depth, ask_depth=ask_depth)


# ──────────────────────────────────────────────────────────────────
# FeeSchedule
# ──────────────────────────────────────────────────────────────────


def test_fee_applies_correctly_buy_vs_sell() -> None:
    fee = FeeSchedule(taker_fee=0.001)
    # BUY pays more
    assert fee.apply(100.0, "BUY", "TAKER") == pytest.approx(100.1)
    # SELL receives less
    assert fee.apply(100.0, "SELL", "TAKER") == pytest.approx(99.9)


def test_negative_maker_fee_acts_as_rebate() -> None:
    """Some venues pay makers — fee=-0.0001 = 1bp rebate."""
    fee = FeeSchedule(maker_fee=-0.0001)
    # BUY with maker fee=-1bp → effective price slightly cheaper
    assert fee.apply(100.0, "BUY", "MAKER") < 100.0


# ──────────────────────────────────────────────────────────────────
# Single-venue routing
# ──────────────────────────────────────────────────────────────────


def test_single_venue_simple_route() -> None:
    quotes = {"binance": _q("binance", 99.5, 100.5, ask_depth=5)}
    plan = route_order("BTC", "BUY", 3.0, quotes)
    assert plan.n_venues_used == 1
    assert plan.fills[0].venue == "binance"
    assert plan.fills[0].quantity == pytest.approx(3.0)
    assert plan.unfilled_quantity == 0.0


def test_single_venue_insufficient_depth_returns_partial_unfilled() -> None:
    quotes = {"binance": _q("binance", 99.5, 100.5, ask_depth=2)}
    plan = route_order("BTC", "BUY", 5.0, quotes)
    assert plan.total_quantity == pytest.approx(2.0)
    assert plan.unfilled_quantity == pytest.approx(3.0)


# ──────────────────────────────────────────────────────────────────
# Multi-venue routing — cheapest first
# ──────────────────────────────────────────────────────────────────


def test_multi_venue_buy_picks_cheapest_ask() -> None:
    quotes = {
        "binance": _q("binance", 99.5, 100.5, ask_depth=10),
        "coinbase": _q("coinbase", 99.5, 100.3, ask_depth=10),  # cheaper
        "upbit":    _q("upbit",   99.5, 100.7, ask_depth=10),
    }
    plan = route_order("BTC", "BUY", 5.0, quotes)
    assert plan.n_venues_used == 1
    assert plan.fills[0].venue == "coinbase"


def test_multi_venue_sell_picks_highest_bid() -> None:
    quotes = {
        "binance": _q("binance", 99.5, 100.5, bid_depth=10),
        "coinbase": _q("coinbase", 99.8, 100.6, bid_depth=10),  # highest bid
        "upbit":    _q("upbit",   99.3, 100.4, bid_depth=10),
    }
    plan = route_order("BTC", "SELL", 5.0, quotes)
    assert plan.fills[0].venue == "coinbase"


def test_multi_venue_split_when_depth_insufficient() -> None:
    """Order bigger than any single-venue top-of-book → split across cheap+next."""
    quotes = {
        "v_cheap":  _q("v_cheap", 99, 100.0, ask_depth=3),
        "v_mid":    _q("v_mid", 99, 100.2, ask_depth=3),
        "v_dear":   _q("v_dear", 99, 100.5, ask_depth=3),
    }
    plan = route_order("BTC", "BUY", 7.0, quotes)
    assert plan.n_venues_used == 3
    # First leg = cheapest
    assert plan.fills[0].venue == "v_cheap"
    assert plan.fills[0].quantity == pytest.approx(3.0)
    # Last leg = dearest (only what's needed: 1)
    assert plan.fills[-1].venue == "v_dear"
    assert plan.fills[-1].quantity == pytest.approx(1.0)
    assert plan.total_quantity == pytest.approx(7.0)
    assert plan.unfilled_quantity == 0.0


def test_fees_change_cheapest_choice() -> None:
    """Raw best_ask is tied → fee schedule breaks the tie."""
    quotes = {
        "a": _q("a", 99, 100.0, ask_depth=10),
        "b": _q("b", 99, 100.0, ask_depth=10),
    }
    fees = {
        "a": FeeSchedule(taker_fee=0.001),   # 10bp
        "b": FeeSchedule(taker_fee=0.0001),  # 1bp
    }
    plan = route_order("BTC", "BUY", 3.0, quotes, fees=fees)
    assert plan.fills[0].venue == "b"


def test_zero_quantity_returns_empty_plan() -> None:
    quotes = {"a": _q("a", 99, 100, ask_depth=10)}
    plan = route_order("BTC", "BUY", 0.0, quotes)
    assert plan.total_quantity == 0.0
    assert plan.unfilled_quantity == 0.0
    assert plan.fills == []


def test_empty_venues_all_unfilled() -> None:
    plan = route_order("BTC", "BUY", 5.0, {})
    assert plan.total_quantity == 0.0
    assert plan.unfilled_quantity == pytest.approx(5.0)


def test_venue_with_zero_depth_is_skipped() -> None:
    quotes = {
        "v_empty": _q("v_empty", 99, 100, ask_depth=0),
        "v_real": _q("v_real", 99, 100.5, ask_depth=10),
    }
    plan = route_order("BTC", "BUY", 3.0, quotes)
    assert plan.fills[0].venue == "v_real"


# ──────────────────────────────────────────────────────────────────
# Cost calculation
# ──────────────────────────────────────────────────────────────────


def test_cost_bp_computed_vs_reference_mid() -> None:
    quotes = {
        "v": _q("v", 99.5, 100.5, ask_depth=10),  # mid = 100
    }
    plan = route_order("BTC", "BUY", 1.0, quotes)
    # effective_price ≈ 100.5 * (1 + 0.0005) ≈ 100.55025
    # cost_bp vs mid 100 ≈ 55bp
    assert plan.fills[0].expected_cost_bp == pytest.approx(55.025, abs=1.0)


def test_weighted_avg_cost_reflects_per_leg_costs() -> None:
    quotes = {
        "cheap": _q("cheap", 99.5, 100.0, ask_depth=2),
        "dear":  _q("dear",  99.5, 101.0, ask_depth=10),
    }
    plan = route_order("BTC", "BUY", 4.0, quotes)
    # Two legs; weighted avg should be between the two leg costs
    assert plan.n_venues_used == 2
    assert min(f.expected_cost_bp for f in plan.fills) <= plan.weighted_avg_cost_bp
    assert plan.weighted_avg_cost_bp <= max(f.expected_cost_bp for f in plan.fills)


# ──────────────────────────────────────────────────────────────────
# Serialization + savings helper
# ──────────────────────────────────────────────────────────────────


def test_to_dict_includes_all_fields() -> None:
    quotes = {"v": _q("v", 99, 100, ask_depth=5)}
    plan = route_order("BTC", "BUY", 3.0, quotes)
    d = plan.to_dict()
    assert {"fills", "total_quantity", "unfilled_quantity",
            "weighted_avg_cost_bp", "n_venues_used", "reference_mid"} <= d.keys()


def test_savings_vs_single_venue_positive_when_routing_helps() -> None:
    quotes = {
        "cheap": _q("cheap", 99, 100.0, ask_depth=10),
        "dear":  _q("dear",  99, 100.5, ask_depth=10),
    }
    plan = route_order("BTC", "BUY", 5.0, quotes)
    savings = savings_vs_single_venue(plan, primary_venue="dear",
                                      venue_quotes=quotes, side="BUY")
    assert savings["savings_bp"] > 0
    assert savings["routed_cost_bp"] < savings["single_venue_cost_bp"]


def test_savings_vs_single_venue_unknown_primary_returns_zero() -> None:
    quotes = {"v": _q("v", 99, 100, ask_depth=5)}
    plan = route_order("BTC", "BUY", 3.0, quotes)
    s = savings_vs_single_venue(plan, "nonexistent", quotes, "BUY")
    assert s == {"savings_bp": 0.0, "savings_usd": 0.0}


# ──────────────────────────────────────────────────────────────────
# Realistic scenario: arb-style market-neutral split
# ──────────────────────────────────────────────────────────────────


def test_arb_scenario_buys_cheap_sells_expensive() -> None:
    """basis_arb wants long-spot/short-perp — verify each leg routes to
    the best venue for that side."""
    quotes = {
        "binance":  _q("binance", 100.0, 100.1, bid_depth=10, ask_depth=10),
        "coinbase": _q("coinbase", 99.9, 100.05, bid_depth=10, ask_depth=10),
        "upbit":    _q("upbit", 100.2, 100.3, bid_depth=10, ask_depth=10),
    }
    buy_plan = route_order("BTC", "BUY", 5.0, quotes)
    sell_plan = route_order("BTC", "SELL", 5.0, quotes)

    # BUY: cheapest ask = coinbase 100.05
    assert buy_plan.fills[0].venue == "coinbase"
    # SELL: highest bid = upbit 100.2
    assert sell_plan.fills[0].venue == "upbit"
