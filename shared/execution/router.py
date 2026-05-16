"""V3 #6 — Cross-Exchange Smart Order Routing.

For a given (symbol, side, quantity), compares quotes across N venues
and returns the cheapest fill plan after fees, optionally splitting
across venues when one venue can't absorb the whole order at the top
of the book.

Used by:
  - basis_arb alpha (V2 #4): emits maker-spot + taker-perp pairs that
    each get routed to the best venue.
  - cost-aware rebalancer (V3 #5): when an order is too big for one
    venue's top-of-book, this splits it.

Pure Python — caller does the venue-API call. The router stays
deterministic given the venue quotes + fee schedule.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional

Side = Literal["BUY", "SELL"]
OrderType = Literal["MAKER", "TAKER"]


# ──────────────────────────────────────────────────────────────────
# Inputs
# ──────────────────────────────────────────────────────────────────


@dataclass
class VenueQuote:
    """Top-of-book snapshot for one venue at one symbol."""

    venue: str
    best_bid: float
    best_ask: float
    bid_depth: float   # quantity available at best_bid
    ask_depth: float   # quantity available at best_ask


@dataclass
class FeeSchedule:
    """Per-venue fee schedule. Positive fees subtract from realized PnL.

    Some venues offer NEGATIVE maker fees (rebate) — set maker_fee < 0
    to model that correctly.
    """

    maker_fee: float = 0.0001  # 1bp default
    taker_fee: float = 0.0005  # 5bp default

    def apply(self, price: float, side: Side, order_type: OrderType) -> float:
        """Effective price after fee — what the trader actually pays/receives."""
        fee = self.taker_fee if order_type == "TAKER" else self.maker_fee
        if side == "BUY":
            return price * (1.0 + fee)
        return price * (1.0 - fee)


# ──────────────────────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────────────────────


@dataclass
class RouterFill:
    """One leg of a (potentially) multi-venue routing plan."""

    venue: str
    quantity: float
    expected_price: float       # quoted price (pre-fee)
    effective_price: float      # after fee
    expected_cost_bp: float     # vs midpoint of the cheapest venue


@dataclass
class RoutingPlan:
    fills: list[RouterFill] = field(default_factory=list)
    total_quantity: float = 0.0
    unfilled_quantity: float = 0.0
    weighted_avg_cost_bp: float = 0.0
    reference_mid: float = 0.0

    @property
    def n_venues_used(self) -> int:
        return len({f.venue for f in self.fills})

    def to_dict(self) -> dict:
        return {
            "fills": [vars(f) for f in self.fills],
            "total_quantity": self.total_quantity,
            "unfilled_quantity": self.unfilled_quantity,
            "weighted_avg_cost_bp": self.weighted_avg_cost_bp,
            "n_venues_used": self.n_venues_used,
            "reference_mid": self.reference_mid,
        }


# ──────────────────────────────────────────────────────────────────
# Routing
# ──────────────────────────────────────────────────────────────────


def _reference_mid(quotes: dict[str, VenueQuote]) -> float:
    """Cross-venue mid — average of (best_bid+best_ask)/2 across venues."""
    mids = []
    for q in quotes.values():
        if q.best_bid > 0 and q.best_ask > 0:
            mids.append((q.best_bid + q.best_ask) / 2.0)
    return sum(mids) / len(mids) if mids else 0.0


def route_order(
    symbol: str,
    side: Side,
    quantity: float,
    venue_quotes: dict[str, VenueQuote],
    *,
    fees: Optional[dict[str, FeeSchedule]] = None,
    order_type: OrderType = "TAKER",
) -> RoutingPlan:
    """Greedy best-execution router.

    Algorithm:
      1. Compute reference mid across venues (for cost-bp calc).
      2. Sort venues by effective price (best first — lowest for BUY,
         highest for SELL).
      3. Walk down the sorted list, consume top-of-book depth at each
         venue until quantity is exhausted.
      4. If quantity remains after all venues exhausted, return it as
         unfilled (caller decides — wait, escalate to limit, partial fill).
    """
    if quantity <= 0:
        return RoutingPlan(reference_mid=_reference_mid(venue_quotes))
    if not venue_quotes:
        return RoutingPlan(unfilled_quantity=quantity)

    fees = fees or {}
    mid = _reference_mid(venue_quotes)

    # Build candidate list: (effective_price, available_qty, raw_price, venue)
    candidates = []
    for venue, q in venue_quotes.items():
        fee = fees.get(venue, FeeSchedule())
        if side == "BUY":
            if q.best_ask <= 0 or q.ask_depth <= 0:
                continue
            effective = fee.apply(q.best_ask, "BUY", order_type)
            candidates.append((effective, q.ask_depth, q.best_ask, venue))
        else:  # SELL
            if q.best_bid <= 0 or q.bid_depth <= 0:
                continue
            effective = fee.apply(q.best_bid, "SELL", order_type)
            # For SELL we want highest effective price → sort descending
            candidates.append((-effective, q.bid_depth, q.best_bid, venue))

    if not candidates:
        return RoutingPlan(unfilled_quantity=quantity, reference_mid=mid)

    # Sort: BUY → ascending effective (cheapest first); SELL is already
    # negated so ascending = highest revenue first.
    candidates.sort(key=lambda x: x[0])

    plan = RoutingPlan(reference_mid=mid)
    remaining = quantity
    total_cost_bp_weighted = 0.0
    for effective_sort_key, avail, raw_price, venue in candidates:
        if remaining <= 0:
            break
        fill_qty = min(remaining, avail)
        if fill_qty <= 0:
            continue
        # Un-negate for SELL to get the true effective price
        effective_price = -effective_sort_key if side == "SELL" else effective_sort_key
        # Cost in bp vs cross-venue mid (positive = adverse)
        if mid > 0:
            if side == "BUY":
                cost_bp = (effective_price - mid) / mid * 10_000.0
            else:
                cost_bp = (mid - effective_price) / mid * 10_000.0
        else:
            cost_bp = 0.0
        plan.fills.append(RouterFill(
            venue=venue,
            quantity=fill_qty,
            expected_price=raw_price,
            effective_price=effective_price,
            expected_cost_bp=cost_bp,
        ))
        total_cost_bp_weighted += cost_bp * fill_qty
        plan.total_quantity += fill_qty
        remaining -= fill_qty

    plan.unfilled_quantity = max(remaining, 0.0)
    if plan.total_quantity > 0:
        plan.weighted_avg_cost_bp = total_cost_bp_weighted / plan.total_quantity
    return plan


# ──────────────────────────────────────────────────────────────────
# Convenience — single-venue savings calc (for ops dashboards)
# ──────────────────────────────────────────────────────────────────


def savings_vs_single_venue(
    plan: RoutingPlan,
    primary_venue: str,
    venue_quotes: dict[str, VenueQuote],
    side: Side,
    *,
    fees: Optional[dict[str, FeeSchedule]] = None,
    order_type: OrderType = "TAKER",
) -> dict:
    """How much cheaper is the routed plan vs hitting only `primary_venue`?

    Returns the bp savings + dollar savings (assumes quantity in base
    asset, * mid_price for $ figure). Useful for proving routing value
    to ops/dashboards.
    """
    fees = fees or {}
    if primary_venue not in venue_quotes:
        return {"savings_bp": 0.0, "savings_usd": 0.0}
    q = venue_quotes[primary_venue]
    fee = fees.get(primary_venue, FeeSchedule())
    if side == "BUY":
        single_eff = fee.apply(q.best_ask, "BUY", order_type)
        single_cost_bp = (
            (single_eff - plan.reference_mid) / plan.reference_mid * 10_000.0
        ) if plan.reference_mid > 0 else 0.0
    else:
        single_eff = fee.apply(q.best_bid, "SELL", order_type)
        single_cost_bp = (
            (plan.reference_mid - single_eff) / plan.reference_mid * 10_000.0
        ) if plan.reference_mid > 0 else 0.0

    savings_bp = single_cost_bp - plan.weighted_avg_cost_bp
    return {
        "savings_bp": round(savings_bp, 4),
        "savings_usd": round(savings_bp * plan.total_quantity * plan.reference_mid / 10_000.0, 6),
        "single_venue_cost_bp": round(single_cost_bp, 4),
        "routed_cost_bp": round(plan.weighted_avg_cost_bp, 4),
    }
