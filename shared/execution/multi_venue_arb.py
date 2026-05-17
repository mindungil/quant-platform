"""V4-6 — Multi-venue live arbitrage executor.

Takes a basis-arb (or any market-neutral pair) alpha signal and turns it
into TWO synchronized orders — long-leg on the cheapest venue + short-leg
on the most-expensive venue, with hard guards from risk_monitor_hub and
capital_tier.

Pair semantics
--------------
A "pair" is just (long_symbol, short_symbol, venue_quotes_long,
venue_quotes_short). For basis_arb this is typically:
  - long spot at venue with cheapest ask
  - short perp at venue with highest bid

The class wraps:
  - smart routing via route_order on each leg
  - capital tier cap on the combined dollar exposure
  - risk monitor hub: HARD kill → reject; SOFT throttle → scale qty
  - execution agent: each leg's order_type via the maker/taker bandit

All hot-path code is deterministic given inputs; the bandit /
risk-hub interactions are explicit (passed via the bandit param) so
tests don't need a global singleton.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shared.execution.maker_taker_bandit import MakerTakerBandit, context_key
from shared.execution.router import (
    FeeSchedule,
    RoutingPlan,
    VenueQuote,
    route_order,
)
from shared.risk import capital_tier
from shared.risk.monitor_hub import RiskEvent, current_size_multiplier, emit, is_killed


@dataclass
class ArbPairOrder:
    """One arbitrage opportunity to execute."""

    long_symbol: str
    short_symbol: str
    requested_quantity: float            # base-asset units (per leg)
    target_notional_usd: float            # informational
    long_venue_quotes: dict[str, VenueQuote]
    short_venue_quotes: dict[str, VenueQuote]
    long_fees: dict[str, FeeSchedule] = field(default_factory=dict)
    short_fees: dict[str, FeeSchedule] = field(default_factory=dict)
    urgency: str = "normal"               # for maker/taker bandit context


@dataclass
class ArbExecutionResult:
    long_plan: Optional[RoutingPlan] = None
    short_plan: Optional[RoutingPlan] = None
    long_action: Optional[str] = None
    short_action: Optional[str] = None
    executed_quantity: float = 0.0
    skipped_reason: Optional[str] = None
    risk_size_multiplier: float = 1.0
    capital_tier: str = "PAPER"
    notional_capped: bool = False

    def summary(self) -> dict:
        return {
            "executed_quantity": self.executed_quantity,
            "skipped_reason": self.skipped_reason,
            "risk_size_multiplier": self.risk_size_multiplier,
            "capital_tier": self.capital_tier,
            "notional_capped": self.notional_capped,
            "long_action": self.long_action,
            "short_action": self.short_action,
            "long_legs": self.long_plan.n_venues_used if self.long_plan else 0,
            "short_legs": self.short_plan.n_venues_used if self.short_plan else 0,
        }


def execute_arb_pair(
    order: ArbPairOrder,
    *,
    bandit: Optional[MakerTakerBandit] = None,
    scope: str = "multi-venue-arb",
) -> ArbExecutionResult:
    """Synchronized two-leg execution. Returns an ArbExecutionResult.

    Pipeline:
      0. Risk hub: is_killed() → reject; current_size_multiplier() → scale qty
      1. Capital tier: cap target notional → derive scaled quantity
      2. Maker/taker bandit: select action per leg (separate context per side)
      3. Smart route: route each leg, fees applied per venue
      4. If either leg's routing has unfilled qty > some tolerance, emit OBS

    Pure function — caller does the actual venue-API POSTs after the
    plan returns.
    """
    result = ArbExecutionResult(
        capital_tier=capital_tier.current_tier(),
        risk_size_multiplier=current_size_multiplier(scope=scope),
    )

    # Phase 0 — risk gates
    if is_killed(scope=scope) or is_killed(scope="global"):
        result.skipped_reason = "risk_hub_hard_kill"
        emit(RiskEvent(
            event_class="OBS",
            reason="arb_skipped_kill",
            scope=scope,
            detail=f"{order.long_symbol}/{order.short_symbol}",
        ))
        return result

    soft_mult = current_size_multiplier(scope=scope)
    base_qty = order.requested_quantity * soft_mult
    if base_qty <= 0:
        result.skipped_reason = "soft_throttle_zero"
        return result

    # Phase 1 — capital tier cap (we cap each leg independently)
    # Use the per-leg notional approximation: qty * avg_long_ask
    asks = [q.best_ask for q in order.long_venue_quotes.values() if q.best_ask > 0]
    avg_ask = sum(asks) / len(asks) if asks else 0.0
    notional_estimate = base_qty * avg_ask if avg_ask > 0 else order.target_notional_usd
    tier_cap = capital_tier.max_order_notional()
    if notional_estimate > tier_cap:
        if avg_ask > 0:
            base_qty = tier_cap / avg_ask
        result.notional_capped = True

    # Phase 2 — maker/taker bandit (per-leg context, separate)
    long_ctx = context_key(
        spread_bp=_avg_spread_bp(order.long_venue_quotes),
        annualized_vol=0.30,
        order_size_usd=notional_estimate,
        urgency=order.urgency,
    )
    short_ctx = context_key(
        spread_bp=_avg_spread_bp(order.short_venue_quotes),
        annualized_vol=0.30,
        order_size_usd=notional_estimate,
        urgency=order.urgency,
    )
    long_action = bandit.select(long_ctx) if bandit else "TAKER"
    short_action = bandit.select(short_ctx) if bandit else "TAKER"
    result.long_action = long_action
    result.short_action = short_action

    # Phase 3 — route each leg
    result.long_plan = route_order(
        symbol=order.long_symbol,
        side="BUY",
        quantity=base_qty,
        venue_quotes=order.long_venue_quotes,
        fees=order.long_fees,
        order_type=long_action,
    )
    result.short_plan = route_order(
        symbol=order.short_symbol,
        side="SELL",
        quantity=base_qty,
        venue_quotes=order.short_venue_quotes,
        fees=order.short_fees,
        order_type=short_action,
    )
    result.executed_quantity = min(
        result.long_plan.total_quantity,
        result.short_plan.total_quantity,
    )

    # Partial-fill warning
    if (result.long_plan.unfilled_quantity > base_qty * 0.05 or
        result.short_plan.unfilled_quantity > base_qty * 0.05):
        emit(RiskEvent(
            event_class="OBS",
            reason="arb_partial_fill_legs",
            scope=scope,
            detail=(
                f"{order.long_symbol}/{order.short_symbol} "
                f"long_unfilled={result.long_plan.unfilled_quantity:.4f} "
                f"short_unfilled={result.short_plan.unfilled_quantity:.4f}"
            ),
        ))

    return result


def _avg_spread_bp(quotes: dict[str, VenueQuote]) -> float:
    """Average bid-ask spread in bp across venues, mid-quoted."""
    spreads = []
    for q in quotes.values():
        if q.best_ask > 0 and q.best_bid > 0:
            mid = (q.best_ask + q.best_bid) / 2.0
            spreads.append((q.best_ask - q.best_bid) / mid * 10_000.0)
    return sum(spreads) / len(spreads) if spreads else 0.0
