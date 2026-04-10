#!/usr/bin/env python3
"""Paper Trading Portfolio Tracker.

Reads signal JSONs from data/signals/, simulates execution at the signal's
price, tracks portfolio positions, PnL, drawdown, and Sharpe over time.

State is persisted to data/paper/portfolio_state.json so it survives restarts.
Each hourly cron run of generate_signals.py → this tracker updates the portfolio.

Usage:
    # Update portfolio with latest signals
    python3 scripts/live/paper_portfolio.py update

    # Show current status
    python3 scripts/live/paper_portfolio.py status

    # Show full history
    python3 scripts/live/paper_portfolio.py history

    # Reset (start fresh)
    python3 scripts/live/paper_portfolio.py reset
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

SIGNALS_DIR = REPO_ROOT / "data" / "signals"
PAPER_DIR = REPO_ROOT / "data" / "paper"
STATE_FILE = PAPER_DIR / "portfolio_state.json"
HISTORY_FILE = PAPER_DIR / "portfolio_history.jsonl"

UTC = timezone.utc

# Paper trading config
INITIAL_CAPITAL = 10_000.0  # $10k starting capital
COST_BPS = 5.0              # taker fee assumption
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "LINKUSDT"]


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "capital": INITIAL_CAPITAL,
        "positions": {s: 0.0 for s in SYMBOLS},
        "prices": {s: 0.0 for s in SYMBOLS},
        "total_pnl": 0.0,
        "total_costs": 0.0,
        "n_trades": 0,
        "peak_equity": INITIAL_CAPITAL,
        "max_drawdown": 0.0,
        "last_update": None,
        "start_time": datetime.now(UTC).isoformat(),
    }


def save_state(state: dict):
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_history(record: dict):
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def get_latest_signals() -> dict[str, dict]:
    """Get the most recent signal for each symbol."""
    files = sorted(SIGNALS_DIR.glob("signals_*.json"), reverse=True)
    for f in files:
        try:
            with open(f) as fh:
                sigs = json.load(fh)
                result = {}
                for s in sigs:
                    if "error" not in s and s.get("symbol"):
                        result[s["symbol"]] = s
                if result:
                    return result
        except Exception:
            continue
    return {}


def update(state: dict) -> dict:
    """Process latest signals, update portfolio."""
    signals = get_latest_signals()
    if not signals:
        print("No signals found.")
        return state

    ts = datetime.now(UTC).isoformat()
    bar_pnl = 0.0
    bar_costs = 0.0
    trades = []

    for sym in SYMBOLS:
        sig = signals.get(sym)
        if not sig:
            continue

        new_price = sig.get("price", 0)
        old_price = state["prices"].get(sym, 0)
        old_pos = state["positions"].get(sym, 0.0)
        new_pos = sig.get("target_position", 0.0)

        # PnL from price movement on existing position
        if old_price > 0 and old_pos != 0:
            price_return = (new_price - old_price) / old_price
            pnl = old_pos * price_return * state["capital"]
            bar_pnl += pnl

        # Trade cost on position change
        delta = abs(new_pos - old_pos)
        if delta > 0.01:  # minimum trade threshold
            cost = delta * state["capital"] * (COST_BPS * 1e-4)
            bar_costs += cost
            state["n_trades"] += 1
            trades.append(f"{sym} {old_pos:+.3f}→{new_pos:+.3f}")

        state["positions"][sym] = new_pos
        state["prices"][sym] = new_price

    # Update capital
    net_pnl = bar_pnl - bar_costs
    state["capital"] += net_pnl
    state["total_pnl"] += net_pnl
    state["total_costs"] += bar_costs

    # Drawdown tracking
    if state["capital"] > state["peak_equity"]:
        state["peak_equity"] = state["capital"]
    dd = (state["peak_equity"] - state["capital"]) / state["peak_equity"]
    if dd > state["max_drawdown"]:
        state["max_drawdown"] = dd

    state["last_update"] = ts

    # Append to history
    record = {
        "timestamp": ts,
        "capital": round(state["capital"], 2),
        "pnl": round(net_pnl, 2),
        "costs": round(bar_costs, 2),
        "positions": {k: round(v, 4) for k, v in state["positions"].items()},
        "trades": trades,
    }
    append_history(record)

    # Print summary
    ret_pct = (state["capital"] / INITIAL_CAPITAL - 1) * 100
    print(f"  Updated: capital=${state['capital']:,.2f} ({ret_pct:+.1f}%)")
    print(f"  Bar PnL=${net_pnl:+.2f}  costs=${bar_costs:.2f}")
    if trades:
        print(f"  Trades: {', '.join(trades)}")
    print(f"  DD={state['max_drawdown']:.1%}  total_trades={state['n_trades']}")

    return state


def status(state: dict):
    ret_pct = (state["capital"] / INITIAL_CAPITAL - 1) * 100
    print(f"\n{'='*50}")
    print(f"  PAPER PORTFOLIO STATUS")
    print(f"{'='*50}")
    print(f"  Capital:    ${state['capital']:>12,.2f}  ({ret_pct:+.1f}%)")
    print(f"  Total PnL:  ${state['total_pnl']:>12,.2f}")
    print(f"  Total Costs:${state['total_costs']:>12,.2f}")
    print(f"  Max DD:     {state['max_drawdown']:>12.1%}")
    print(f"  Trades:     {state['n_trades']:>12}")
    print(f"  Start:      {state.get('start_time', '?')}")
    print(f"  Last update:{state.get('last_update', 'never')}")
    print(f"\n  Positions:")
    for sym in SYMBOLS:
        pos = state["positions"].get(sym, 0)
        price = state["prices"].get(sym, 0)
        notional = abs(pos) * state["capital"]
        if abs(pos) > 0.01:
            print(f"    {sym:10s} pos={pos:+.3f}  price=${price:>10,.2f}  notional=${notional:>10,.2f}")
        else:
            print(f"    {sym:10s} FLAT")
    net_exp = sum(state["positions"].get(s, 0) for s in SYMBOLS)
    print(f"\n  Net exposure: {net_exp:+.3f}")
    print(f"{'='*50}")


def history():
    if not HISTORY_FILE.exists():
        print("No history yet.")
        return
    records = []
    with open(HISTORY_FILE) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    if not records:
        print("No history yet.")
        return

    print(f"\n  Paper Trading History ({len(records)} updates)")
    print(f"  {'Timestamp':25s} {'Capital':>12s} {'PnL':>10s} {'Costs':>8s} Trades")
    print(f"  {'-'*70}")
    for r in records[-20:]:  # last 20
        ts = r["timestamp"][:19]
        trades_str = ", ".join(r.get("trades", [])) if r.get("trades") else "-"
        print(f"  {ts:25s} ${r['capital']:>10,.2f} ${r['pnl']:>8,.2f} ${r['costs']:>6,.2f} {trades_str}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["update", "status", "history", "reset"])
    args = ap.parse_args()

    if args.command == "reset":
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
        print("Portfolio reset.")
        return 0

    state = load_state()

    if args.command == "update":
        state = update(state)
        save_state(state)
    elif args.command == "status":
        status(state)
    elif args.command == "history":
        history()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
