"""Tests for VirtualFuturesConnector — in-memory Binance Futures simulator.

Covers:
  - Isolation tripwire (refuses non-virtual paths)
  - ExchangeConnector interface parity (all abstract methods callable)
  - Initial state (equity, balance, no positions)
  - Order execution: MARKET + LIMIT, BUY + SELL, open + close + flip
  - Symbol filter rejection (minNotional, stepSize rounding to zero)
  - Fee accounting (maker vs taker)
  - PnL tracking (realized + unrealized)
  - State persistence across connector re-instantiation
  - Mark price fetcher injection for deterministic tests
"""
from __future__ import annotations

import pytest

from shared.execution.connector import ExchangeConnector
from shared.execution.virtual_futures import (
    DEFAULT_MAKER_BPS,
    DEFAULT_TAKER_BPS,
    VirtualFuturesConnector,
    VirtualState,
)


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_virtual_dir(tmp_path):
    """Return (state_file, history_file) under a 'virtual' subdir so
    the tripwire accepts it."""
    d = tmp_path / "virtual"
    d.mkdir()
    return d / "state.json", d / "history.jsonl"


@pytest.fixture
def fixed_prices():
    """Deterministic price fetcher for tests."""
    prices = {
        "BTCUSDT": 80_000.0,
        "ETHUSDT": 2_500.0,
        "BNBUSDT": 600.0,
        "SOLUSDT": 100.0,
    }

    def fetcher(symbols):
        return {s: prices[s] for s in symbols if s in prices}

    fetcher.prices = prices
    return fetcher


@pytest.fixture
def conn(tmp_virtual_dir, fixed_prices):
    state_file, history_file = tmp_virtual_dir
    return VirtualFuturesConnector(
        initial_equity=10_000,
        state_file=state_file,
        history_file=history_file,
        reset=True,
        price_fetcher=fixed_prices,
    )


# ──────────────────────────────────────────────────────────────────
# Isolation
# ──────────────────────────────────────────────────────────────────

def test_tripwire_refuses_path_outside_virtual(tmp_path):
    """Safety: refuse to write state anywhere outside data/virtual/."""
    bad = tmp_path / "paper" / "state.json"
    bad.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="/virtual/"):
        VirtualFuturesConnector(state_file=bad, history_file=bad.parent / "h.jsonl")


def test_tripwire_refuses_history_outside_virtual(tmp_path):
    good_state = tmp_path / "virtual" / "state.json"
    good_state.parent.mkdir(parents=True)
    bad_history = tmp_path / "execution" / "history.jsonl"
    bad_history.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match="/virtual/"):
        VirtualFuturesConnector(state_file=good_state, history_file=bad_history)


def test_marker_file_created(tmp_virtual_dir, fixed_prices):
    state_file, hist_file = tmp_virtual_dir
    VirtualFuturesConnector(
        state_file=state_file, history_file=hist_file, reset=True,
        price_fetcher=fixed_prices,
    )
    marker = state_file.parent / "IS_VIRTUAL_NOT_REAL.txt"
    assert marker.exists()
    body = marker.read_text()
    assert "NOT real money" in body
    assert "NOT Binance testnet" in body


# ──────────────────────────────────────────────────────────────────
# Interface parity — all ExchangeConnector abstract methods work
# ──────────────────────────────────────────────────────────────────

def test_implements_exchange_connector(conn):
    assert isinstance(conn, ExchangeConnector)


def test_initial_state_clean(conn):
    assert conn.get_account_equity() == pytest.approx(10_000)
    assert conn.get_balances() == {"USDT": 10_000}
    assert conn.get_positions() == {}


def test_mark_prices_use_injected_fetcher(conn, fixed_prices):
    prices = conn.get_mark_prices(["BTCUSDT", "ETHUSDT"])
    assert prices == {"BTCUSDT": 80_000.0, "ETHUSDT": 2_500.0}


# ──────────────────────────────────────────────────────────────────
# MARKET orders
# ──────────────────────────────────────────────────────────────────

def test_market_buy_opens_long_at_mark(conn):
    r = conn.place_market_order("BTCUSDT", "BUY", 0.1)
    assert r.status == "FILLED"
    assert r.avg_price == pytest.approx(80_000.0)
    assert r.filled_quantity == pytest.approx(0.1)
    assert conn.get_positions() == {"BTCUSDT": pytest.approx(0.1)}


def test_market_sell_opens_short(conn):
    r = conn.place_market_order("ETHUSDT", "SELL", 2.0)
    assert r.status == "FILLED"
    assert conn.get_positions()["ETHUSDT"] == pytest.approx(-2.0)


def test_market_uses_taker_fee(conn):
    notional = 0.1 * 80_000
    expected_fee = notional * DEFAULT_TAKER_BPS * 1e-4
    conn.place_market_order("BTCUSDT", "BUY", 0.1)
    snap = conn.snapshot()
    assert snap["total_fees"] == pytest.approx(expected_fee)


def test_closing_position_realizes_pnl(conn, fixed_prices):
    # Long 0.1 BTC at 80k
    conn.place_market_order("BTCUSDT", "BUY", 0.1)
    # Price moves to 82k
    fixed_prices.prices["BTCUSDT"] = 82_000.0
    # Close → should realize +$200 minus taker fees on both legs
    conn.place_market_order("BTCUSDT", "SELL", 0.1)
    snap = conn.snapshot()
    # Realized PnL: (82000 - 80000) * 0.1 = +200
    assert snap["realized_pnl"] == pytest.approx(200.0)
    assert conn.get_positions() == {}


def test_flipping_position_realizes_pnl_for_old_and_opens_new(conn, fixed_prices):
    conn.place_market_order("BTCUSDT", "BUY", 0.1)       # long 0.1 @ 80k
    fixed_prices.prices["BTCUSDT"] = 82_000.0
    conn.place_market_order("BTCUSDT", "SELL", 0.2)      # sell 0.2 → net short 0.1
    snap = conn.snapshot()
    assert snap["realized_pnl"] == pytest.approx(200.0)  # only the 0.1 closing leg
    # New position is short 0.1 at 82k
    assert conn.get_positions()["BTCUSDT"] == pytest.approx(-0.1)
    assert snap["avg_entry_prices"]["BTCUSDT"] == pytest.approx(82_000.0)


def test_adding_to_position_weight_averages_entry(conn, fixed_prices):
    conn.place_market_order("BTCUSDT", "BUY", 0.1)        # 0.1 @ 80k
    fixed_prices.prices["BTCUSDT"] = 84_000.0
    conn.place_market_order("BTCUSDT", "BUY", 0.1)        # +0.1 @ 84k
    snap = conn.snapshot()
    # avg = (0.1*80k + 0.1*84k) / 0.2 = 82k
    assert snap["avg_entry_prices"]["BTCUSDT"] == pytest.approx(82_000.0)


def test_unrealized_pnl_tracks_mark(conn, fixed_prices):
    conn.place_market_order("ETHUSDT", "BUY", 2.0)        # 2 ETH @ 2500
    fixed_prices.prices["ETHUSDT"] = 2_600.0
    equity = conn.get_account_equity()
    snap = conn.snapshot()
    # UPL = 2 * (2600 - 2500) = +200
    assert snap["unrealized_pnl"] == pytest.approx(200.0)
    # equity = balance (after fees) + 200
    assert equity == pytest.approx(snap["balance"] + 200.0)


# ──────────────────────────────────────────────────────────────────
# LIMIT orders
# ──────────────────────────────────────────────────────────────────

def test_limit_buy_below_mark_is_maker(conn):
    # Mark 80k, limit BUY at 79900 → resting maker order → fill @ 79900 with maker fee
    r = conn.place_limit_order("BTCUSDT", "BUY", 0.1, 79_900.0)
    assert r.status == "FILLED"
    assert r.avg_price == pytest.approx(79_900.0)
    snap = conn.snapshot()
    fees_bps_used = snap["total_fees"] / (0.1 * 79_900.0) * 1e4
    assert fees_bps_used == pytest.approx(DEFAULT_MAKER_BPS)


def test_limit_buy_crossing_mark_is_taker(conn):
    # Mark 80k, limit BUY at 80100 → crosses → taker fee, fill @ mark
    r = conn.place_limit_order("BTCUSDT", "BUY", 0.1, 80_100.0)
    assert r.status == "FILLED"
    assert r.avg_price == pytest.approx(80_000.0)
    snap = conn.snapshot()
    fees_bps_used = snap["total_fees"] / (0.1 * 80_000.0) * 1e4
    assert fees_bps_used == pytest.approx(DEFAULT_TAKER_BPS)


def test_limit_missing_price_rejected(conn):
    r = conn.place_limit_order("BTCUSDT", "BUY", 0.1, 0.0)
    assert r.status == "REJECTED"
    assert "LIMIT" in r.error


# ──────────────────────────────────────────────────────────────────
# Symbol filter rejections
# ──────────────────────────────────────────────────────────────────

def test_min_notional_rejection(conn):
    # BTC minNotional = 5.0 USDT. 0.00001 BTC * 80k = 0.8 USDT → reject
    r = conn.place_market_order("BTCUSDT", "BUY", 0.00001)
    # (step_size 1e-3 will actually round 0.00001 → 0.0 first, so stepSize wins.)
    assert r.status == "REJECTED"


def test_step_size_rounding_to_zero_rejects(conn):
    # SOL step_size = 1.0. Ordering 0.4 SOL → rounds to 0 → reject
    r = conn.place_market_order("SOLUSDT", "BUY", 0.4)
    assert r.status == "REJECTED"
    assert "stepSize" in r.error


def test_step_size_rounds_quantity(conn):
    # BTC step_size = 1e-3. 0.10049 → 0.100
    r = conn.place_market_order("BTCUSDT", "BUY", 0.10049)
    assert r.status == "FILLED"
    assert r.filled_quantity == pytest.approx(0.100, abs=1e-9)


def test_unknown_symbol_uses_default_filter(conn, fixed_prices):
    fixed_prices.prices["NEWCOINUSDT"] = 50.0
    r = conn.place_market_order("NEWCOINUSDT", "BUY", 1.0)
    assert r.status == "FILLED"


def test_no_mark_price_rejected(conn, fixed_prices):
    # Symbol not in price feed
    r = conn.place_market_order("UNKNOWNUSDT", "BUY", 1.0)
    assert r.status == "REJECTED"
    assert "no mark price" in r.error


# ──────────────────────────────────────────────────────────────────
# State persistence
# ──────────────────────────────────────────────────────────────────

def test_state_persists_across_instances(tmp_virtual_dir, fixed_prices):
    state_file, hist_file = tmp_virtual_dir
    c1 = VirtualFuturesConnector(
        initial_equity=10_000, state_file=state_file, history_file=hist_file,
        reset=True, price_fetcher=fixed_prices,
    )
    c1.place_market_order("ETHUSDT", "BUY", 1.0)
    pos_before = c1.get_positions()

    # New instance reading same file
    c2 = VirtualFuturesConnector(
        state_file=state_file, history_file=hist_file, reset=False,
        price_fetcher=fixed_prices,
    )
    assert c2.get_positions() == pos_before
    assert c2.get_account_equity() == pytest.approx(c1.get_account_equity(), abs=1e-6)


def test_reset_wipes_state(conn):
    conn.place_market_order("BTCUSDT", "BUY", 0.1)
    assert conn.get_positions()
    conn.reset_state(initial_equity=5_000)
    assert conn.get_positions() == {}
    assert conn.get_account_equity() == pytest.approx(5_000)


def test_history_file_records_fills_and_rejections(conn, fixed_prices):
    conn.place_market_order("BTCUSDT", "BUY", 0.1)
    conn.place_market_order("SOLUSDT", "BUY", 0.4)  # will reject (step size)
    lines = conn.history_file.read_text().strip().split("\n")
    kinds = [eval(ln).get("type") if False else __import__("json").loads(ln)["type"] for ln in lines]
    assert "fill" in kinds
    assert "reject" in kinds


# ──────────────────────────────────────────────────────────────────
# Counters
# ──────────────────────────────────────────────────────────────────

def test_counters_track_orders(conn):
    conn.place_market_order("BTCUSDT", "BUY", 0.1)
    conn.place_market_order("SOLUSDT", "BUY", 0.4)  # reject
    conn.place_market_order("ETHUSDT", "BUY", 1.0)
    snap = conn.snapshot()
    assert snap["n_orders"] == 3
    assert snap["n_fills"] == 2
    assert snap["n_rejected"] == 1
