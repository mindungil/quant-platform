"""Tests for L2 orderbook helpers + impact cost model."""
from __future__ import annotations

import pytest

from shared.execution.impact_model import estimate_impact, max_safe_slice
from shared.execution.upbit_l2 import (
    OrderbookSnapshot,
    _parse_upbit_orderbook,
    depth_at_bps,
    estimate_queue_position,
    sweep_fill_price,
)


def _book_at_100m(depth_per_level: float = 0.1, n: int = 10) -> OrderbookSnapshot:
    """Synthetic book: mid = 100M KRW, 10 levels each way, 5-bps spacing."""
    mid = 100_000_000.0
    spread = mid * 5e-4  # 5 bps
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for i in range(n):
        bp = mid - spread / 2 - i * (mid * 1e-4)
        ap = mid + spread / 2 + i * (mid * 1e-4)
        bids.append((bp, depth_per_level))
        asks.append((ap, depth_per_level))
    return OrderbookSnapshot(market="KRW-BTC", timestamp_ms=0, bids=bids, asks=asks)


def test_snapshot_parse_round_trip():
    raw = {
        "market": "KRW-BTC",
        "timestamp": 1_700_000_000_000,
        "orderbook_units": [
            {"bid_price": 99, "bid_size": 1.0, "ask_price": 101, "ask_size": 1.0},
            {"bid_price": 98, "bid_size": 2.0, "ask_price": 102, "ask_size": 2.0},
        ],
    }
    snap = _parse_upbit_orderbook(raw)
    assert snap.best_bid == 99
    assert snap.best_ask == 101
    assert snap.mid == 100
    assert len(snap.bids) == 2
    assert len(snap.asks) == 2


def test_depth_at_bps_walks_book():
    snap = _book_at_100m(depth_per_level=0.1, n=5)
    # Each level is 100M KRW * 0.1 = 10M at various prices. 10 bps wide cap
    # should include ~2 levels of asks.
    depth = depth_at_bps(snap, "BUY", max_bps=20)
    # At least 2 levels worth (~20M KRW)
    assert depth > 15_000_000


def test_sweep_fill_price_gives_higher_vwap_for_larger_buy():
    snap = _book_at_100m(depth_per_level=0.1, n=10)
    vwap_small, _ = sweep_fill_price(snap, "BUY", 5_000_000)
    vwap_large, _ = sweep_fill_price(snap, "BUY", 50_000_000)
    assert vwap_large > vwap_small


def test_estimate_impact_small_order_bounded_by_half_spread():
    snap = _book_at_100m(depth_per_level=10.0, n=20)  # deep book
    est = estimate_impact(snap, "BUY", notional_krw=1_000_000)
    # 5-bps spread book → impact at least half-spread (2.5bps)
    assert est.expected_impact_bps >= 2.0
    assert est.expected_impact_bps <= 15.0  # small vs deep book
    assert est.fill_method == "within_book"


def test_estimate_impact_exceeds_book_uses_extrapolation():
    snap = _book_at_100m(depth_per_level=0.0001, n=3)  # tiny book
    # Total visible depth ~ 3 * 100M * 0.0001 = 30k KRW. Request 10M.
    est = estimate_impact(snap, "BUY", notional_krw=10_000_000)
    assert est.fill_method == "extrapolated_sqrt"
    assert est.expected_impact_bps > 50.0


def test_max_safe_slice_is_monotone_in_impact_cap():
    snap = _book_at_100m(depth_per_level=1.0, n=15)
    s_low = max_safe_slice(snap, "BUY", max_impact_bps=5.0)
    s_high = max_safe_slice(snap, "BUY", max_impact_bps=50.0)
    assert s_high >= s_low


def test_queue_position_counts_ahead_at_better_prices():
    snap = _book_at_100m(depth_per_level=0.1, n=5)
    # Resting a bid one level below best → one level ahead
    second_best = snap.bids[1][0]
    ahead = estimate_queue_position(snap, "BUY", second_best)
    # At least the full size of best bid (which is at higher price)
    assert ahead > snap.best_bid * snap.bids[0][1] * 0.99
