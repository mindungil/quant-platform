"""Linear + square-root impact cost model from L2 orderbook depth.

Given an OrderbookSnapshot and a target notional, estimate the expected
impact in bps assuming the order walks the book. Two tiers:

  - **Walk-the-book** (exact): we literally VWAP through the L2 depth.
    Best estimate when the book is fully observed and order size ≤ depth.

  - **Extrapolated square-root** (fallback): when notional exceeds visible
    depth we extrapolate impact with a square-root-of-participation rule
    anchored on the observed slope at the last visible level. This covers
    the case where the top N ticks don't contain enough liquidity.

The extrapolator is calibrated with one parameter `k_sqrt` (default 0.5)
which you can adjust empirically from live ledger drift.
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.execution.upbit_l2 import OrderbookSnapshot, sweep_fill_price


@dataclass
class ImpactEstimate:
    notional_krw: float
    expected_impact_bps: float
    fill_method: str            # "within_book" | "extrapolated_sqrt"
    visible_depth_krw: float
    mid: float
    spread_bps: float


def estimate_impact(
    snapshot: OrderbookSnapshot,
    side: str,
    notional_krw: float,
    k_sqrt: float = 0.5,
) -> ImpactEstimate:
    """Return the expected adverse impact in bps for a market sweep."""
    mid = snapshot.mid or 0.0
    spread_bps = snapshot.spread_bps or 0.0
    if mid <= 0 or notional_krw <= 0:
        return ImpactEstimate(
            notional_krw=notional_krw,
            expected_impact_bps=0.0,
            fill_method="degenerate",
            visible_depth_krw=0.0,
            mid=mid,
            spread_bps=spread_bps,
        )

    # 1. Try walk-the-book first
    vwap, filled = sweep_fill_price(snapshot, side, notional_krw)
    visible = sum(p * s for p, s in (snapshot.asks if side.upper() == "BUY" else snapshot.bids))

    if filled >= notional_krw * 0.999 and vwap > 0:
        # Full fill within visible book
        if side.upper() == "BUY":
            impact_bps = (vwap - mid) / mid * 1e4
        else:
            impact_bps = (mid - vwap) / mid * 1e4
        # Floor at half the spread (the spread cost is part of mid-relative impact)
        return ImpactEstimate(
            notional_krw=notional_krw,
            expected_impact_bps=round(float(max(impact_bps, 0.5 * spread_bps)), 2),
            fill_method="within_book",
            visible_depth_krw=visible,
            mid=mid,
            spread_bps=spread_bps,
        )

    # 2. Order exceeds visible book → square-root extrapolation
    # Base impact = what we'd pay to clear visible depth
    if vwap > 0:
        if side.upper() == "BUY":
            base_bps = (vwap - mid) / mid * 1e4
        else:
            base_bps = (mid - vwap) / mid * 1e4
        base_bps = max(base_bps, 0.5 * spread_bps)
    else:
        base_bps = max(0.5 * spread_bps, 10.0)

    # Extrapolate: impact ≈ base + k_sqrt * spread_bps * sqrt(excess / visible)
    excess = max(notional_krw - visible, 0.0)
    if visible > 0 and excess > 0:
        tail_bps = k_sqrt * max(spread_bps, 2.0) * (excess / visible) ** 0.5 * 10.0
    else:
        tail_bps = 0.0
    total = base_bps + tail_bps

    return ImpactEstimate(
        notional_krw=notional_krw,
        expected_impact_bps=round(float(total), 2),
        fill_method="extrapolated_sqrt",
        visible_depth_krw=visible,
        mid=mid,
        spread_bps=spread_bps,
    )


def max_safe_slice(
    snapshot: OrderbookSnapshot,
    side: str,
    max_impact_bps: float,
    k_sqrt: float = 0.5,
) -> float:
    """Largest notional slice whose expected impact ≤ max_impact_bps.

    Binary-search over notional in [min_order, total_visible_depth].
    """
    visible = sum(p * s for p, s in (snapshot.asks if side.upper() == "BUY" else snapshot.bids))
    if visible <= 0 or not snapshot.mid:
        return 0.0

    lo, hi = 1_000.0, visible * 1.5
    best = 0.0
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        est = estimate_impact(snapshot, side, mid, k_sqrt=k_sqrt)
        if est.expected_impact_bps <= max_impact_bps:
            best = mid
            lo = mid
        else:
            hi = mid
    return best
