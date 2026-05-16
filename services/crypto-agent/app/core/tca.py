"""Transaction Cost Analysis helpers — used by outcome_consumer to feed the
Multi-Armed Bandit with cost-aware rewards instead of raw PnL.

The bandit's old reward signal was:
    reward = pnl  (when nonzero)
          OR (fill_price - reference_price) / reference_price  (slippage proxy)

That has two failure modes:
1. A formula that produces a 0.1% expected edge with 0.2% mean slippage
   looks profitable on the raw PnL signal but actually loses money.
2. The fallback "slippage as reward" mixes sign conventions — for a SELL,
   higher fill is *good* (better price), but the formula treats fill > ref
   as bad.

TCA-adjusted reward:
    realized_slippage = (signed) bps from reference to fill, oriented so
                        positive = adverse to the trader
    tca_cost = tca_cost_weight * abs(realized_slippage)
    reward   = pnl - tca_cost     (when pnl is known)
             OR -tca_cost          (no realized PnL yet; pure cost penalty)

Why penalize *absolute* slippage and not signed: even when a SELL gets a
better-than-reference fill, that's noise — the formula didn't *choose*
the favorable slippage, the execution engine did. We want the bandit to
prefer formulas whose decisions execute with low absolute deviation from
the price they were generated against.

Pure functions — no I/O, no globals. Trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Side = Literal["BUY", "SELL", "buy", "sell"]


@dataclass
class TCAResult:
    """All components of the TCA decomposition, exposed for logging/metrics."""

    raw_pnl: float
    realized_slippage_bp: float   # signed; positive = adverse to trader
    tca_cost_bp: float            # cost charged to the reward (always ≥ 0)
    tca_adjusted_reward: float    # final reward fed to the bandit
    reward_source: str            # "pnl_minus_tca" | "tca_only" | "none"


def compute_realized_slippage_bp(
    fill_price: float,
    reference_price: float,
    side: Side,
) -> float:
    """Signed slippage in basis points oriented so positive = adverse.

    For a BUY, paying more than reference is adverse → positive.
    For a SELL, receiving less than reference is adverse → positive.
    Returns 0.0 when reference is missing/zero.
    """
    if reference_price <= 0 or fill_price <= 0:
        return 0.0
    raw = (fill_price - reference_price) / reference_price * 10_000.0
    side_u = side.upper() if isinstance(side, str) else "BUY"
    return raw if side_u == "BUY" else -raw


def compute_tca_reward(
    *,
    pnl: float,
    fill_price: float,
    reference_price: float,
    side: Side,
    tca_cost_weight: float = 1.0,
) -> TCAResult:
    """Combine realized PnL with TCA penalty to produce a bandit-ready reward.

    Parameters
    ----------
    pnl : realized PnL of the trade (fractional return, e.g. 0.012 = +1.2%).
          Pass 0.0 when the trade hasn't been closed out yet — the function
          falls back to a pure-cost reward (negative of tca penalty), which
          encourages the bandit away from high-slippage formulas even
          before profitability is known.
    fill_price : actual execution price.
    reference_price : price snapshot at signal generation (decision-time).
    side : 'BUY' or 'SELL' (case-insensitive).
    tca_cost_weight : multiplier on the slippage penalty. 1.0 = bps-for-bps;
                      0.0 disables the TCA correction entirely (returns raw
                      pnl as reward); higher values make the bandit more
                      slippage-averse.
    """
    if tca_cost_weight < 0:
        raise ValueError(f"tca_cost_weight must be ≥ 0, got {tca_cost_weight}")

    slip_bp = compute_realized_slippage_bp(fill_price, reference_price, side)
    cost_bp = abs(slip_bp) * tca_cost_weight
    # Convert cost from bp to fraction so it's on the same scale as pnl
    cost_frac = cost_bp / 10_000.0

    if pnl != 0:
        reward = pnl - cost_frac
        source = "pnl_minus_tca"
    elif slip_bp != 0:
        # No PnL data — penalize known cost so high-slippage formulas
        # still get negative reinforcement.
        reward = -cost_frac
        source = "tca_only"
    else:
        reward = 0.0
        source = "none"

    return TCAResult(
        raw_pnl=float(pnl),
        realized_slippage_bp=float(slip_bp),
        tca_cost_bp=float(cost_bp),
        tca_adjusted_reward=float(reward),
        reward_source=source,
    )
