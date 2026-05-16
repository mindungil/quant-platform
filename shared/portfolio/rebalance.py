"""V3 #5 — Real-Time Cost-Aware Portfolio Rebalancer.

Why this exists
---------------
The meta_ensemble.combine() output gives a *target* position per asset.
The actual portfolio drifts from that target as prices move, alphas
turn over, and partial fills leave us with awkward residuals. Naive
rebalance ("always go to target every bar") burns money on the
bid/ask spread + market impact every cycle.

This module decides — per symbol, per cycle — whether the drift is
worth correcting given expected execution cost. If yes, it returns a
slicing plan (single-shot or N-chunk TWAP) ready to hand to the
order-service.

Inputs
------
- current_positions    : {symbol: held_qty}      (notional units, e.g. BTC)
- target_positions     : {symbol: target_qty}    (from meta_ensemble)
- market_data per symbol:
    mid_price          : current mid (USD)
    spread_bp          : current best-bid/best-ask spread in bp
    adv_usd            : 30-day avg daily volume in USD
- expected_alpha_per_position_bps: {symbol: bps-per-bar gain if at target}
- cycle config (min drift, chunk sizing, lookahead bars)

Decision policy
---------------
For each symbol:
  drift_qty   = target - current
  drift_usd   = drift_qty * mid_price
  drift_pct   = |drift_qty| / max(|target|, |current|, 1e-9)
  if drift_pct < min_drift_pct:           SKIP_SMALL_DRIFT
  expected_gain_bps = alpha_per_position_bps[s] * |drift_qty| * bars_lookahead
  expected_cost_bps = spread_bp/2 + impact_bps(drift_usd, adv_usd)
  if expected_gain_bps < expected_cost_bps: SKIP_COST_EXCEEDS_GAIN
  → EXECUTE with chunk_count = ceil(drift_usd / max_chunk_usd)

Where impact_bps uses the classic square-root market-impact model
calibrated against ADV (Almgren-Chriss style):
    impact_bp = sqrt(participation) * 10_000 * 0.0014
with participation = drift_usd / adv_usd. The 0.0014 constant is the
typical fit from public studies; production should override with
exchange-specific fit.

Pure Python — caller does the order-service RPC. No I/O here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional

Side = Literal["BUY", "SELL"]
OrderType = Literal["LIMIT", "MARKET"]
Action = Literal["EXECUTE", "SKIP_SMALL_DRIFT", "SKIP_COST_EXCEEDS_GAIN", "SKIP_NO_DRIFT"]


# ──────────────────────────────────────────────────────────────────
# Output dataclasses
# ──────────────────────────────────────────────────────────────────


@dataclass
class RebalanceOrder:
    symbol: str
    side: Side
    quantity: float        # absolute, units of base asset
    order_type: OrderType  # MAKER/TAKER hint — actual route via execution bandit
    chunks: int            # 1 = single shot, >1 = TWAP slice count
    chunk_quantity: float  # per-chunk quantity (total / chunks)
    target_price_hint: float
    reason: str


@dataclass
class RebalanceDecision:
    """Per-symbol audit record. Always returned, even when SKIP."""

    symbol: str
    action: Action
    drift_qty: float
    drift_usd: float
    drift_pct: float
    expected_gain_bp: float
    expected_cost_bp: float
    chunks: int
    reason: str


@dataclass
class RebalancePlan:
    orders: list[RebalanceOrder] = field(default_factory=list)
    decisions: list[RebalanceDecision] = field(default_factory=list)

    @property
    def n_executed(self) -> int:
        return len(self.orders)

    @property
    def n_skipped(self) -> int:
        return sum(1 for d in self.decisions if d.action != "EXECUTE")

    def to_dict(self) -> dict:
        return {
            "orders": [vars(o) for o in self.orders],
            "decisions": [vars(d) for d in self.decisions],
            "n_executed": self.n_executed,
            "n_skipped": self.n_skipped,
        }


# ──────────────────────────────────────────────────────────────────
# Cost model
# ──────────────────────────────────────────────────────────────────


def square_root_impact_bp(
    trade_usd: float,
    adv_usd: float,
    *,
    coefficient: float = 14.0,
) -> float:
    """Almgren-Chriss style sqrt-impact model.

    impact_bp = coefficient * sqrt(participation_rate)
    where participation_rate = trade_usd / adv_usd.
    coefficient=14 is a typical fit for liquid US equities; crypto
    perp markets tend to be 1.5-2× higher. Override per venue.
    """
    if adv_usd <= 0 or trade_usd <= 0:
        return 0.0
    participation = trade_usd / adv_usd
    return coefficient * math.sqrt(participation)


def total_cost_bp(
    trade_usd: float,
    spread_bp: float,
    adv_usd: float,
    *,
    impact_coefficient: float = 14.0,
) -> float:
    """Round-trip-style transaction-cost estimate."""
    return spread_bp / 2.0 + square_root_impact_bp(
        trade_usd, adv_usd, coefficient=impact_coefficient
    )


# ──────────────────────────────────────────────────────────────────
# Plan
# ──────────────────────────────────────────────────────────────────


def plan_rebalance(
    current_positions: dict[str, float],
    target_positions: dict[str, float],
    *,
    market_data: dict[str, dict],
    expected_alpha_per_position_bps: Optional[dict[str, float]] = None,
    min_drift_pct: float = 0.05,
    max_chunk_pct_of_adv: float = 0.005,
    bars_lookahead: int = 60,
    impact_coefficient: float = 14.0,
    default_order_type: OrderType = "LIMIT",
) -> RebalancePlan:
    """Return per-symbol orders + audit decisions.

    Symbols can appear in current_positions, target_positions, or both;
    a symbol-only-in-target means open a new position, only-in-current
    means close.
    """
    plan = RebalancePlan()
    alpha_bps = expected_alpha_per_position_bps or {}

    all_symbols = set(current_positions) | set(target_positions)
    for symbol in sorted(all_symbols):
        current = float(current_positions.get(symbol, 0.0))
        target = float(target_positions.get(symbol, 0.0))
        drift_qty = target - current

        md = market_data.get(symbol, {})
        mid = float(md.get("mid_price", 0.0))
        spread_bp = float(md.get("spread_bp", 0.0))
        adv = float(md.get("adv_usd", 0.0))

        drift_usd = drift_qty * mid
        denom = max(abs(target), abs(current), 1e-9)
        drift_pct = abs(drift_qty) / denom

        # No drift at all
        if abs(drift_qty) < 1e-12:
            plan.decisions.append(RebalanceDecision(
                symbol=symbol, action="SKIP_NO_DRIFT",
                drift_qty=0.0, drift_usd=0.0, drift_pct=0.0,
                expected_gain_bp=0.0, expected_cost_bp=0.0, chunks=0,
                reason="current == target",
            ))
            continue

        # Below the drift threshold → not worth touching
        if drift_pct < min_drift_pct:
            plan.decisions.append(RebalanceDecision(
                symbol=symbol, action="SKIP_SMALL_DRIFT",
                drift_qty=drift_qty, drift_usd=drift_usd, drift_pct=drift_pct,
                expected_gain_bp=0.0, expected_cost_bp=0.0, chunks=0,
                reason=f"drift {drift_pct:.2%} < min {min_drift_pct:.2%}",
            ))
            continue

        cost_bp = total_cost_bp(
            abs(drift_usd), spread_bp, adv, impact_coefficient=impact_coefficient,
        )
        per_position_alpha = alpha_bps.get(symbol, 0.0)
        # Expected gain: alpha_bps_per_bar × |target_qty_in_drift| × bars,
        # divided by 1 to keep bps units. (drift_qty is in base asset
        # units; alpha_bps_per_position assumes per-unit-position bps.)
        expected_gain_bp = abs(per_position_alpha) * abs(drift_qty) * bars_lookahead

        if expected_gain_bp < cost_bp:
            plan.decisions.append(RebalanceDecision(
                symbol=symbol, action="SKIP_COST_EXCEEDS_GAIN",
                drift_qty=drift_qty, drift_usd=drift_usd, drift_pct=drift_pct,
                expected_gain_bp=expected_gain_bp, expected_cost_bp=cost_bp, chunks=0,
                reason=f"cost {cost_bp:.2f}bp > gain {expected_gain_bp:.2f}bp",
            ))
            continue

        # Decide slicing
        max_chunk_usd = max_chunk_pct_of_adv * adv if adv > 0 else abs(drift_usd)
        if max_chunk_usd <= 0:
            chunks = 1
        else:
            chunks = max(1, math.ceil(abs(drift_usd) / max_chunk_usd))
        per_chunk_qty = abs(drift_qty) / chunks
        side: Side = "BUY" if drift_qty > 0 else "SELL"

        plan.orders.append(RebalanceOrder(
            symbol=symbol, side=side, quantity=abs(drift_qty),
            order_type=default_order_type, chunks=chunks,
            chunk_quantity=per_chunk_qty, target_price_hint=mid,
            reason=f"gain {expected_gain_bp:.2f}bp > cost {cost_bp:.2f}bp",
        ))
        plan.decisions.append(RebalanceDecision(
            symbol=symbol, action="EXECUTE",
            drift_qty=drift_qty, drift_usd=drift_usd, drift_pct=drift_pct,
            expected_gain_bp=expected_gain_bp, expected_cost_bp=cost_bp,
            chunks=chunks, reason="executing",
        ))

    return plan
