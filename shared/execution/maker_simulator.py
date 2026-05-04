"""Maker-only execution simulator for backtests.

Bar-level model: when target position changes, post a passive limit at
the bar's mid. Fill iff the next bar's range crosses the limit (the
market came to us). Unfilled orders age K bars then optionally aggress.

This is the bridge between "subtract X bps from returns" toy modeling
and a real maker microstructure: it captures (a) maker rebate, (b) the
fill-vs-miss tradeoff that rebates buy you, (c) adverse selection
(filled fills are conditional on the market crossing — exactly the
selection bias that makes naive fee swaps too optimistic).

Limitations (kept honest):
  * No L2 queue position — fill is binary on cross
  * No partial fills inside a bar
  * Slippage at aggressive fallback uses spread + half-impact estimate
  * Single venue, no smart-order-routing
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MakerCosts:
    maker_fee_bps: float = -1.0      # negative = rebate
    taker_fee_bps: float = 4.0       # fallback aggression cost
    half_spread_bps: float = 1.0     # mid → posted limit offset (we cross half-spread to taker if aggressing)
    impact_bps_per_unit: float = 2.0 # extra cost per unit of |delta_position| when aggressing


# Binance Futures fee tiers (approximate, as of 2026-Q1)
FEE_TIERS: dict[str, dict[str, float]] = {
    "VIP0": {"maker_fee_bps": 2.0, "taker_fee_bps": 4.0},
    "VIP1": {"maker_fee_bps": 1.6, "taker_fee_bps": 4.0},
    "VIP2": {"maker_fee_bps": 1.4, "taker_fee_bps": 3.5},
    "VIP3": {"maker_fee_bps": 1.2, "taker_fee_bps": 3.2},
    "VIP4": {"maker_fee_bps": 1.0, "taker_fee_bps": 3.0},
    "VIP5": {"maker_fee_bps": 0.8, "taker_fee_bps": 2.7},
    "VIP6": {"maker_fee_bps": 0.6, "taker_fee_bps": 2.5},
    "VIP7": {"maker_fee_bps": 0.4, "taker_fee_bps": 2.2},
    "VIP8": {"maker_fee_bps": 0.2, "taker_fee_bps": 2.0},
    "VIP9": {"maker_fee_bps": 0.0, "taker_fee_bps": 1.7},
    "MAKER_REBATE": {"maker_fee_bps": -1.0, "taker_fee_bps": 4.0},  # maker-rebate program
}


def costs_from_tier(tier: str, **overrides) -> "MakerCosts":
    """Create MakerCosts from a fee tier name. Unknown tiers fall back to VIP0."""
    t = FEE_TIERS.get(tier.upper(), FEE_TIERS["VIP0"])
    return MakerCosts(maker_fee_bps=t["maker_fee_bps"], taker_fee_bps=t["taker_fee_bps"], **overrides)


@dataclass
class MakerPolicy:
    max_age_bars: int = 4            # cancel after this many bars unfilled
    aggress_on_cancel: bool = True   # if False, drop the trade and keep stale position
    min_change: float = 1e-4         # ignore tiny target deltas
    partial_fill: bool = False       # enable volume-proportional partial fills
    fill_participation: float = 0.05 # max fraction of bar volume we can fill
    reprice_on_age: int = 0          # if >0, cancel & re-post at current close after N bars (chase)
    queue_model: bool = False        # probabilistic fill based on volume at touch
    queue_depth_factor: float = 0.10 # prob = min(1, bar_vol * factor / |order_qty|)
    ioc: bool = False                # immediate-or-cancel: fill on placement bar or cancel


@dataclass
class MakerFillReport:
    realized_position: pd.Series      # actual position after fills/misses
    bar_returns: pd.Series            # net per-bar returns including fees
    n_orders: int
    n_maker_fills: int
    n_aggressed: int
    n_dropped: int
    fill_rate: float
    avg_age_to_fill: float
    adverse_selection_bps: float = 0.0  # mean 1-bar markout after maker fills (negative = adverse)


def simulate_maker_execution(
    target_position: pd.Series,
    ohlc: pd.DataFrame,
    *,
    costs: MakerCosts | None = None,
    policy: MakerPolicy | None = None,
    funding_rate_hourly: float = 0.0000125,
) -> MakerFillReport:
    """Simulate maker-first execution against bar-level OHLC.

    Args:
        target_position: signal-driven target position per bar, indexed
            on the same timeline as ``ohlc``. Range is unitless (e.g. -1..+1).
        ohlc: must contain 'open', 'high', 'low', 'close'. Index aligned
            to ``target_position``.

    Returns:
        MakerFillReport with the *realized* position path (which may lag
        target when limits don't fill) and the corresponding net returns.
    """
    costs = costs or MakerCosts()
    policy = policy or MakerPolicy()

    idx = target_position.index
    n = len(idx)
    target = target_position.to_numpy(dtype=float)
    o = ohlc["open"].to_numpy(dtype=float)
    h = ohlc["high"].to_numpy(dtype=float)
    low = ohlc["low"].to_numpy(dtype=float)
    c = ohlc["close"].to_numpy(dtype=float)
    _need_vol = policy.partial_fill or policy.queue_model
    vol = ohlc["volume"].to_numpy(dtype=float) if "volume" in ohlc.columns and _need_vol else None

    realized = np.zeros(n)
    bar_ret = np.zeros(n)
    open_order = None  # (side, qty, limit_price, age, placed_at_idx)

    n_orders = n_maker = n_aggressed = n_dropped = 0
    age_sum = 0
    markouts: list[float] = []  # 1-bar markout in bps after each maker fill

    for i in range(n):
        prev_pos = realized[i - 1] if i > 0 else 0.0
        # Mark-to-market on prior position (close-to-close return on bar i)
        if i > 0:
            ret_i = (c[i] - c[i - 1]) / c[i - 1] if c[i - 1] != 0 else 0.0
            bar_ret[i] += prev_pos * ret_i
            bar_ret[i] -= abs(prev_pos) * funding_rate_hourly

        # Try to fill outstanding order against this bar's range
        new_pos = prev_pos
        if open_order is not None:
            side, qty, lim, age, placed_at = open_order
            crossed = (side == "buy" and low[i] <= lim) or (side == "sell" and h[i] >= lim)
            # Queue model: even if price crosses, fill only with prob ∝ volume/order_size
            if crossed and policy.queue_model and vol is not None and vol[i] > 0:
                fill_prob = min(1.0, vol[i] * policy.queue_depth_factor / max(abs(qty), 1e-9))
                # Deterministic: use fill_prob as fill fraction (no RNG for reproducibility)
                crossed = fill_prob > 0.5  # binary threshold
            if crossed:
                # Partial-fill: cap qty to participation × bar_volume
                fill_qty = qty
                if vol is not None and vol[i] > 0:
                    max_fill = policy.fill_participation * vol[i]
                    if abs(qty) > max_fill:
                        fill_qty = max_fill if qty > 0 else -max_fill
                fill_cost = abs(fill_qty) * (costs.maker_fee_bps / 1e4)
                bar_ret[i] -= fill_cost
                if c[i] != 0:
                    bar_ret[i] += fill_qty * (c[i] - lim) / c[i]
                new_pos = prev_pos + fill_qty
                n_maker += 1
                age_sum += (i - placed_at)
                # 1-bar markout: direction × (close[i+1] - fill_price) / fill_price
                if i + 1 < n and lim > 0:
                    sign = 1.0 if side == "buy" else -1.0
                    mo = sign * (c[i + 1] - lim) / lim * 1e4  # in bps
                    markouts.append(mo)
                remainder = qty - fill_qty
                if abs(remainder) >= policy.min_change:
                    open_order = (side, remainder, lim, age, placed_at)
                else:
                    open_order = None
            else:
                age += 1
                effective_max_age = 1 if policy.ioc else policy.max_age_bars
                if age >= effective_max_age:
                    if policy.aggress_on_cancel:
                        # Aggress at this bar's close with taker fee + spread + impact
                        cost_bps = (
                            costs.taker_fee_bps
                            + costs.half_spread_bps
                            + costs.impact_bps_per_unit * abs(qty)
                        )
                        bar_ret[i] -= abs(qty) * (cost_bps / 1e4)
                        new_pos = prev_pos + qty
                        n_aggressed += 1
                    else:
                        n_dropped += 1
                    open_order = None
                elif policy.reprice_on_age > 0 and age >= policy.reprice_on_age:
                    # Cancel/replace: re-post at current close (chase)
                    shift = (costs.half_spread_bps / 1e4) * c[i]
                    new_lim = c[i] - shift if side == "buy" else c[i] + shift
                    open_order = (side, qty, new_lim, 0, i)  # reset age
                    n_orders += 1  # counts as new order
                else:
                    open_order = (side, qty, lim, age, placed_at)

        # Decide whether to post a new order this bar
        # (only if no order outstanding — single-order book per symbol)
        delta = target[i] - new_pos
        if open_order is None and abs(delta) >= policy.min_change:
            side = "buy" if delta > 0 else "sell"
            # Post at mid (close) shifted by half-spread in our favor
            shift = (costs.half_spread_bps / 1e4) * c[i]
            lim = c[i] - shift if side == "buy" else c[i] + shift
            open_order = (side, delta, lim, 0, i)
            n_orders += 1

        realized[i] = new_pos

    fill_rate = n_maker / n_orders if n_orders else 0.0
    avg_age = age_sum / n_maker if n_maker else 0.0
    adv_sel = float(np.mean(markouts)) if markouts else 0.0

    return MakerFillReport(
        realized_position=pd.Series(realized, index=idx),
        bar_returns=pd.Series(bar_ret, index=idx),
        n_orders=n_orders,
        n_maker_fills=n_maker,
        n_aggressed=n_aggressed,
        n_dropped=n_dropped,
        fill_rate=fill_rate,
        avg_age_to_fill=avg_age,
        adverse_selection_bps=round(adv_sel, 2),
    )
