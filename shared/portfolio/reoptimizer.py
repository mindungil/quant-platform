"""V4-4 — Real-Time Portfolio Reoptimizer.

Stitches together V3 modules (meta_ensemble + rebalance + smart_router)
into a single end-to-end function the strategy-lab worker can call once
per N-minute cycle:

  1. Pull latest alpha positions + bar returns from the ledger.
  2. Run meta_ensemble.combine() → target_position per asset.
  3. Compare against current_positions → plan_rebalance() with
     cost-awareness.
  4. For each rebalance order, route via router.route_order() across
     available venues.
  5. Emit risk events through monitor_hub if any leg fails / hits a kill.

Pure orchestration — no DB / NATS / venue I/O on the hot path. Caller
passes in the snapshot data (alpha_positions, current_positions,
venue_quotes, bar_returns); the function returns a ReoptResult with
the plan + routing decisions + any risk events that were emitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from shared.execution.router import (
    FeeSchedule,
    RoutingPlan,
    VenueQuote,
    route_order,
)
from shared.portfolio.meta_ensemble import (
    MetaEnsembleConfig,
    combine,
)
from shared.portfolio.rebalance import (
    RebalancePlan,
    plan_rebalance,
)
from shared.risk import capital_tier
from shared.risk.monitor_hub import RiskEvent, emit


@dataclass
class ReoptInput:
    """All data the reoptimizer needs for one cycle (no I/O inside)."""

    alpha_positions: pd.DataFrame      # index=ts, columns=alpha names, vals in [-1, 1]
    bar_returns: pd.Series             # index=ts, single underlying or composite return
    current_positions: dict[str, float]   # {symbol: qty} we actually hold
    market_data: dict[str, dict]       # for rebalance: {symbol: {mid_price, spread_bp, adv_usd}}
    venue_quotes: dict[str, dict[str, VenueQuote]]  # {symbol: {venue: VenueQuote}}
    regime: Optional[pd.Series] = None
    fees: Optional[dict[str, FeeSchedule]] = None
    expected_alpha_per_position_bps: Optional[dict[str, float]] = None
    config: MetaEnsembleConfig = field(default_factory=MetaEnsembleConfig)


@dataclass
class ReoptResult:
    target_positions: dict[str, float]
    rebalance_plan: RebalancePlan
    routing_plans: dict[str, RoutingPlan]    # {symbol: RoutingPlan}
    capital_tier: str
    tier_capped_orders: int
    risk_events_emitted: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "target_positions_count": len(self.target_positions),
            "n_orders": self.rebalance_plan.n_executed,
            "n_skipped": self.rebalance_plan.n_skipped,
            "venues_used": sum(p.n_venues_used for p in self.routing_plans.values()),
            "total_unfilled_qty": sum(p.unfilled_quantity for p in self.routing_plans.values()),
            "capital_tier": self.capital_tier,
            "tier_capped_orders": self.tier_capped_orders,
            "risk_events": len(self.risk_events_emitted),
        }


def reoptimize(inp: ReoptInput) -> ReoptResult:
    """Run one reoptimization cycle. Pure function — caller does I/O."""
    # Phase 1 — meta_ensemble → combined target position per bar
    combine_out = combine(
        inp.alpha_positions,
        inp.bar_returns,
        regime=inp.regime,
        config=inp.config,
    )
    combined_position = combine_out["position"]
    # Realize the last bar's signal as the target — caller is responsible
    # for mapping single-asset combined position to per-symbol target_qty.
    # If positions are passed as one column per symbol, sum is the
    # per-symbol target.
    if isinstance(combined_position, pd.Series) and not combined_position.empty:
        last_target = float(combined_position.iloc[-1])
    else:
        last_target = 0.0
    # For multi-symbol, alpha_positions columns may include per-symbol
    # weight; the caller maps these through their own portfolio model.
    # Default behavior: scale current holdings toward last_target uniformly.
    target_positions = {
        sym: last_target * max(abs(qty), 1.0) if last_target != 0 else 0.0
        for sym, qty in inp.current_positions.items()
    }
    # Symbols only in venue_quotes (new entries) → open with target_qty proportional
    for sym in inp.venue_quotes:
        target_positions.setdefault(sym, last_target)

    # Phase 2 — rebalance plan (cost-aware drift gate)
    plan = plan_rebalance(
        current_positions=inp.current_positions,
        target_positions=target_positions,
        market_data=inp.market_data,
        expected_alpha_per_position_bps=inp.expected_alpha_per_position_bps,
    )

    # Phase 2.5 — tier cap each order
    tier_cap_usd = capital_tier.max_order_notional()
    tier_capped = 0
    for order in plan.orders:
        mid = float(inp.market_data.get(order.symbol, {}).get("mid_price", 0))
        if mid <= 0:
            continue
        order_usd = order.quantity * mid
        if order_usd > tier_cap_usd:
            new_qty = tier_cap_usd / mid
            order.quantity = new_qty
            order.chunk_quantity = new_qty / max(order.chunks, 1)
            tier_capped += 1

    # Phase 3 — route each order through the cheapest venue mix
    routing_plans: dict[str, RoutingPlan] = {}
    risk_events: list[str] = []

    for order in plan.orders:
        venues = inp.venue_quotes.get(order.symbol)
        if not venues:
            risk_events.append(f"no_venue_quotes_{order.symbol}")
            emit(RiskEvent(
                event_class="OBS",
                reason="no_venue_quotes",
                scope=f"reopt:{order.symbol}",
                detail=f"order qty={order.quantity} side={order.side}",
            ))
            continue

        routing_plans[order.symbol] = route_order(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            venue_quotes=venues,
            fees=inp.fees,
            order_type="MAKER",  # default — overridden by maker/taker bandit upstream
        )
        if routing_plans[order.symbol].unfilled_quantity > 0:
            risk_events.append(f"partial_fill_{order.symbol}")

    return ReoptResult(
        target_positions=target_positions,
        rebalance_plan=plan,
        routing_plans=routing_plans,
        capital_tier=capital_tier.current_tier(),
        tier_capped_orders=tier_capped,
        risk_events_emitted=risk_events,
    )
