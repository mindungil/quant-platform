"""Upbit L2 orderbook utilities.

Provides three layers:

  1. UpbitL2Fetcher — REST snapshot via /v1/orderbook (WebSocket streaming is
     out-of-scope for this module; the REST endpoint refreshes at ~100ms
     cadence and is enough for pre-trade impact estimation).
  2. OrderbookSnapshot — canonical dataclass normalizing the Upbit response
     (bids/asks as (price, size) tuples sorted best-first).
  3. depth_at_bps(snapshot, side, max_bps) — cumulative tradeable liquidity
     within `max_bps` of the touch, in KRW notional.
  4. estimate_queue_position(snapshot, side, limit_price) — rough estimate of
     how much KRW notional sits ahead of your resting order at the given
     price level (used by smart_upbit to decide whether to requote).

All helpers are pure functions over the snapshot so unit tests can construct
synthetic books without hitting the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from shared.execution.upbit import UpbitConnector, _to_market


@dataclass
class OrderbookSnapshot:
    market: str
    timestamp_ms: int
    bids: list[tuple[float, float]] = field(default_factory=list)   # (price, size) — DESC by price
    asks: list[tuple[float, float]] = field(default_factory=list)   # (price, size) — ASC by price

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return 0.5 * (self.best_bid + self.best_ask)

    @property
    def spread_bps(self) -> float | None:
        bb, ba, mid = self.best_bid, self.best_ask, self.mid
        if bb is None or ba is None or not mid:
            return None
        return (ba - bb) / mid * 1e4


def _parse_upbit_orderbook(payload: dict) -> OrderbookSnapshot:
    """Convert /v1/orderbook response element → OrderbookSnapshot.

    Upbit returns `orderbook_units` as a list of rows, each row has
    bid_price/bid_size/ask_price/ask_size. Rows are ASC by ask_price from
    best; we split into bid/ask lists sorted best-first.
    """
    units = payload.get("orderbook_units", []) or []
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for u in units:
        bp = float(u.get("bid_price", 0.0))
        bs = float(u.get("bid_size", 0.0))
        ap = float(u.get("ask_price", 0.0))
        asz = float(u.get("ask_size", 0.0))
        if bp > 0 and bs > 0:
            bids.append((bp, bs))
        if ap > 0 and asz > 0:
            asks.append((ap, asz))
    bids.sort(key=lambda x: -x[0])  # descending: best bid first
    asks.sort(key=lambda x: x[0])    # ascending: best ask first
    return OrderbookSnapshot(
        market=payload.get("market", ""),
        timestamp_ms=int(payload.get("timestamp", 0)),
        bids=bids,
        asks=asks,
    )


class UpbitL2Fetcher:
    """Thin wrapper around UpbitConnector for orderbook snapshots.

    Calls the public `/v1/orderbook` endpoint — no auth needed, but still
    rate-limited via the connector's limiter.
    """

    def __init__(self, connector: UpbitConnector) -> None:
        self._c = connector

    def fetch(self, symbol: str) -> OrderbookSnapshot | None:
        market = _to_market(symbol)
        try:
            raw = self._c._request("GET", "/v1/orderbook", params={"markets": market})
        except Exception:
            return None
        if not raw:
            return None
        item = raw[0] if isinstance(raw, list) else raw
        return _parse_upbit_orderbook(item)


# ---- pure helpers (no network) ----


def depth_at_bps(
    snapshot: OrderbookSnapshot,
    side: str,
    max_bps: float,
) -> float:
    """Cumulative KRW notional within `max_bps` of the touch on the given side.

    For a BUY we walk asks from best upward while `(p - best_ask) / mid * 1e4 <= max_bps`.
    For a SELL we walk bids from best downward while `(best_bid - p) / mid * 1e4 <= max_bps`.
    """
    mid = snapshot.mid
    if not mid:
        return 0.0
    levels: Sequence[tuple[float, float]]
    if side.upper() == "BUY":
        ref = snapshot.best_ask or mid
        levels = snapshot.asks
        def within(p: float) -> bool:
            return (p - ref) / mid * 1e4 <= max_bps
    else:
        ref = snapshot.best_bid or mid
        levels = snapshot.bids
        def within(p: float) -> bool:
            return (ref - p) / mid * 1e4 <= max_bps

    total_krw = 0.0
    for price, size in levels:
        if not within(price):
            break
        total_krw += price * size
    return total_krw


def sweep_fill_price(
    snapshot: OrderbookSnapshot,
    side: str,
    notional_krw: float,
) -> tuple[float, float]:
    """Simulate a market order of `notional_krw` and return (vwap, filled_notional).

    Walks the given side book cumulatively. If the book is thinner than the
    requested notional, returns the vwap of what *could* be filled and the
    filled amount (so callers can detect starvation).
    """
    if notional_krw <= 0:
        return (0.0, 0.0)
    levels = snapshot.asks if side.upper() == "BUY" else snapshot.bids
    remaining = notional_krw
    cost = 0.0
    filled_notional = 0.0
    for price, size in levels:
        level_notional = price * size
        take = min(level_notional, remaining)
        cost += take  # we pay `take` KRW at this level
        filled_notional += take
        remaining -= take
        if remaining <= 1e-6:
            break
    # Compute vwap: filled_notional is in KRW, but quantity is filled_notional/price_level.
    # We recompute cleanly from fill shares.
    shares = 0.0
    remaining = notional_krw
    for price, size in levels:
        level_notional = price * size
        take = min(level_notional, remaining)
        shares += take / price
        remaining -= take
        if remaining <= 1e-6:
            break
    vwap = (filled_notional / shares) if shares > 0 else 0.0
    return (vwap, filled_notional)


def estimate_queue_position(
    snapshot: OrderbookSnapshot,
    side: str,
    limit_price: float,
) -> float:
    """Rough KRW notional sitting *ahead of us* if we rest at `limit_price`.

    For a BUY at limit_price, "ahead of us" = bids at higher price + existing
    quantity at our price level (FIFO — we join the back of the queue).
    """
    if side.upper() == "BUY":
        ahead = 0.0
        for p, s in snapshot.bids:
            if p > limit_price:
                ahead += p * s
            elif abs(p - limit_price) < 1e-9:
                ahead += p * s
                break
            else:
                break
        return ahead
    else:
        ahead = 0.0
        for p, s in snapshot.asks:
            if p < limit_price:
                ahead += p * s
            elif abs(p - limit_price) < 1e-9:
                ahead += p * s
                break
            else:
                break
        return ahead
