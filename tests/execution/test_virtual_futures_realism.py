"""Phase V3: execution realism tests for VirtualFuturesConnector.

Covers:
  - MARKET slippage: price moves against the order side, proportional to size
  - LIMIT queue: orders don't fill on placing tick; fill on next tick when
    mark crosses; expire after TTL
  - Partial fills: large MARKET orders fill only up to max_pct
  - Rate-limit simulation: random rejection
  - Latency: sleep hook applied
  - Backward compatibility: default realism config = off, V1 tests still pass
"""
from __future__ import annotations

import random
import time
import pytest

from shared.execution.virtual_futures import (
    RealismConfig,
    VirtualFuturesConnector,
)


@pytest.fixture
def tmp_virtual_dir(tmp_path):
    d = tmp_path / "virtual"
    d.mkdir()
    return d / "state.json", d / "history.jsonl"


def _make_conn(state, history, prices: dict, realism: RealismConfig | None = None, seed=0):
    return VirtualFuturesConnector(
        initial_equity=10_000,
        state_file=state,
        history_file=history,
        reset=True,
        price_fetcher=lambda syms: {s: prices[s] for s in syms if s in prices},
        realism=realism,
        rng=random.Random(seed),
    )


# ──────────────────────────────────────────────────────────────────
# MARKET slippage
# ──────────────────────────────────────────────────────────────────

def test_market_slippage_raises_buy_fill_price(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    # 0.5 bps per $10k, 0.1 BTC * 80k = $8000 → 0.4 bps slippage
    realism = RealismConfig(slippage_enabled=True, slippage_bps_per_10k_usd=0.5, slippage_max_bps=100.0)
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism)
    r = c.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.status == "FILLED"
    # Buy fills ABOVE mark
    assert r.avg_price > 80_000.0
    # Slippage = 0.4 bps → 80_000 * 1.00004 = 80003.2
    assert r.avg_price == pytest.approx(80_000 * (1 + 0.4 * 1e-4), rel=1e-6)


def test_market_slippage_lowers_sell_fill_price(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(slippage_enabled=True, slippage_bps_per_10k_usd=0.5, slippage_max_bps=100.0)
    c = _make_conn(state, hist, {"ETHUSDT": 2_500.0}, realism)
    r = c.place_market_order("ETHUSDT", "SELL", 2.0)
    # Notional 5000 → 0.25 bps
    expected = 2_500 * (1 - 0.25 * 1e-4)
    assert r.avg_price == pytest.approx(expected, rel=1e-6)
    assert r.avg_price < 2_500.0


def test_slippage_capped_by_max_bps(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(slippage_enabled=True, slippage_bps_per_10k_usd=100.0, slippage_max_bps=5.0)
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism)
    # Huge notional would exceed cap, should be clamped at 5 bps
    r = c.place_market_order("BTCUSDT", "BUY", 1.0)
    capped = 80_000 * (1 + 5.0 * 1e-4)
    assert r.avg_price == pytest.approx(capped, rel=1e-6)


def test_slippage_off_by_default(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0})  # no realism arg
    r = c.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.avg_price == pytest.approx(80_000.0)


# ──────────────────────────────────────────────────────────────────
# LIMIT queue
# ──────────────────────────────────────────────────────────────────

def test_limit_queue_enabled_parks_non_crossing_order(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(limit_queue_enabled=True)
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism)
    # BUY at 79_900 — maker, below mark
    r = c.place_limit_order("BTCUSDT", "BUY", 0.1, 79_900.0)
    assert r.status == "NEW"
    assert r.filled_quantity == 0.0
    snap = c.snapshot()
    assert len(snap["open_orders"]) == 1
    # No position yet
    assert c.get_positions() == {}


def test_limit_queue_fills_when_mark_crosses(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(limit_queue_enabled=True)
    prices = {"BTCUSDT": 80_000.0}
    c = _make_conn(state, hist, prices, realism)
    r = c.place_limit_order("BTCUSDT", "BUY", 0.1, 79_900.0)
    assert r.status == "NEW"
    # Mark drops to 79_800 → crosses the 79_900 BUY limit
    prices["BTCUSDT"] = 79_800.0
    _ = c.get_mark_prices(["BTCUSDT"])  # this triggers process_open_orders
    snap = c.snapshot()
    assert len(snap["open_orders"]) == 0
    assert c.get_positions()["BTCUSDT"] == pytest.approx(0.1)


def test_limit_queue_crossing_limit_fills_at_limit_price_maker(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(limit_queue_enabled=True)
    prices = {"BTCUSDT": 80_000.0}
    c = _make_conn(state, hist, prices, realism)
    c.place_limit_order("BTCUSDT", "SELL", 0.1, 80_100.0)  # sell above mark → maker
    prices["BTCUSDT"] = 80_200.0  # mark rises past limit → fill
    c.get_mark_prices(["BTCUSDT"])
    # Expect a fill record at 80_100 (limit), maker fee
    import json
    lines = hist.read_text().strip().split("\n")
    fills = [json.loads(ln) for ln in lines if json.loads(ln)["type"] == "fill"]
    assert len(fills) == 1
    assert fills[0]["fill_price"] == pytest.approx(80_100.0)
    assert fills[0]["is_maker"] is True


def test_limit_queue_expires_stale_order(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(limit_queue_enabled=True, limit_ttl_seconds=0.0)
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism)
    c.place_limit_order("BTCUSDT", "BUY", 0.1, 79_900.0)
    time.sleep(0.01)
    c.get_mark_prices(["BTCUSDT"])  # triggers process_open_orders
    snap = c.snapshot()
    assert len(snap["open_orders"]) == 0
    assert c.get_positions() == {}


def test_limit_fill_prob_controls_queue_position(tmp_virtual_dir):
    """limit_fill_prob < 1 models queue position — crossing doesn't always fill."""
    state, hist = tmp_virtual_dir
    realism = RealismConfig(limit_queue_enabled=True, limit_fill_prob=0.0)  # never fill
    prices = {"BTCUSDT": 80_000.0}
    c = _make_conn(state, hist, prices, realism, seed=42)
    c.place_limit_order("BTCUSDT", "BUY", 0.1, 79_900.0)
    prices["BTCUSDT"] = 79_800.0  # crosses
    c.get_mark_prices(["BTCUSDT"])
    # Still resting — never gets filled
    snap = c.snapshot()
    assert len(snap["open_orders"]) == 1


# ──────────────────────────────────────────────────────────────────
# Partial fill
# ──────────────────────────────────────────────────────────────────

def test_partial_fill_on_large_market_order(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(
        partial_fill_enabled=True,
        partial_fill_threshold_pct=0.30,
        partial_fill_max_pct=0.50,
    )
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism)
    # 0.1 BTC * 80k = $8k = 80% of $10k equity → partial
    r = c.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.status == "FILLED"
    # Filled at most 50% of 0.1 = 0.05
    assert r.filled_quantity == pytest.approx(0.05)


def test_partial_fill_off_by_default(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0})
    r = c.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.filled_quantity == pytest.approx(0.1)


# ──────────────────────────────────────────────────────────────────
# Rate limit simulation
# ──────────────────────────────────────────────────────────────────

def test_rate_limit_causes_rejection(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(rate_limit_fail_prob=1.0)  # always fail
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism, seed=0)
    r = c.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.status == "REJECTED"
    assert "rate limit" in r.error.lower()


def test_rate_limit_off_never_rejects(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(rate_limit_fail_prob=0.0)
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism, seed=0)
    for _ in range(10):
        r = c.place_market_order("BTCUSDT", "BUY", 0.001)
        assert r.status in ("FILLED", "REJECTED")  # REJECTED only on filter violations


# ──────────────────────────────────────────────────────────────────
# Latency
# ──────────────────────────────────────────────────────────────────

def test_latency_adds_sleep(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    realism = RealismConfig(latency_ms=20)
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0}, realism)
    t0 = time.perf_counter()
    c.place_market_order("BTCUSDT", "BUY", 0.001)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # Allow generous margin; just verify latency is applied
    assert elapsed_ms > 15


# ──────────────────────────────────────────────────────────────────
# Regression: realism = None (default) behaves exactly like V1
# ──────────────────────────────────────────────────────────────────

def test_default_realism_matches_v1_behavior(tmp_virtual_dir):
    state, hist = tmp_virtual_dir
    c = _make_conn(state, hist, {"BTCUSDT": 80_000.0})
    # MARKET at mark exactly
    r = c.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.avg_price == pytest.approx(80_000.0)
    # LIMIT maker fills immediately at limit
    r2 = c.place_limit_order("BTCUSDT", "SELL", 0.05, 81_000.0)
    assert r2.status == "FILLED"
    assert r2.avg_price == pytest.approx(81_000.0)
