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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from shared.engine.config import load_config  # noqa: E402

SIGNALS_DIR = REPO_ROOT / "data" / "signals"
PAPER_DIR = REPO_ROOT / "data" / "paper"
STATE_FILE = PAPER_DIR / "portfolio_state.json"
HISTORY_FILE = PAPER_DIR / "portfolio_history.jsonl"
CONFIG_PATH = REPO_ROOT / "config" / "v4_production.json"

UTC = timezone.utc

# Paper trading config
INITIAL_CAPITAL = 10_000.0  # $10k starting capital
COST_BPS = 5.0              # taker fee assumption

# Funding cost simulation (perp) — opt-in via PAPER_SIM_FUNDING env.
# Default is OFF only to preserve the in-progress 2026-04-25 → 2026-04-30 soak
# baseline. After 2026-04-30 the default flips to ON; the post-soak default
# is enforced by `live_readiness.py`, which refuses to bless a go-live with
# funding-disabled paper data.
#
# When enabled, an *amortized* funding charge runs on every bar:
#   bar_funding = sum_sym(position_qty × price × funding_rate × Δt/8h)
# Funding rate per symbol is read from the per-symbol baseline mean of
# data/funding/{sym}_funding.csv. Live mode uses Binance's actual settle.
_SOAK_END_ISO = "2026-04-30T03:30:00+00:00"
_now_after_soak = datetime.now(UTC) >= datetime.fromisoformat(_SOAK_END_ISO)
_default_funding = "true" if _now_after_soak else "false"
SIM_FUNDING = os.environ.get("PAPER_SIM_FUNDING", _default_funding).lower() == "true"
FUNDING_DIR = REPO_ROOT / "data" / "funding"
_FUNDING_CACHE: dict[str, float] = {}  # symbol → mean per-8h rate


def _all_tracked_symbols(cfg) -> list[str]:
    """Union of active + parked symbols (so parked positions get liquidated)."""
    return list(dict.fromkeys(list(cfg.symbols) + list(cfg.symbols_parked.keys())))


def load_state(cfg=None) -> dict:
    symbols = _all_tracked_symbols(cfg) if cfg is not None else [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"
    ]
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)
        for s in symbols:
            state.setdefault("positions", {}).setdefault(s, 0.0)
            state.setdefault("prices", {}).setdefault(s, 0.0)
        return state
    return {
        "capital": INITIAL_CAPITAL,
        "positions": {s: 0.0 for s in symbols},
        "prices": {s: 0.0 for s in symbols},
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


def _funding_rate_for(symbol: str) -> float:
    """Return per-8h baseline mean funding rate for symbol. Cached."""
    if symbol in _FUNDING_CACHE:
        return _FUNDING_CACHE[symbol]
    path = FUNDING_DIR / f"{symbol}_funding.csv"
    if not path.exists():
        _FUNDING_CACHE[symbol] = 0.0
        return 0.0
    try:
        # Lightweight parse — avoid pandas dependency in hot path
        rates: list[float] = []
        with path.open() as fh:
            header = fh.readline()
            for line in fh:
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    try:
                        rates.append(float(parts[2]))
                    except ValueError:
                        pass
        rate = sum(rates) / len(rates) if rates else 0.0
    except Exception:
        rate = 0.0
    _FUNDING_CACHE[symbol] = rate
    return rate


def _bar_funding_cost(state: dict, prev_update_iso: str | None) -> float:
    """Amortized funding charge for the bar. Returns total dollar cost
    (positive = paid by the portfolio). Uses per-symbol baseline rate so
    paper sim matches the *backtest cost regime*; live mode uses actual
    settle from Binance.
    """
    if not prev_update_iso:
        return 0.0
    try:
        prev = datetime.fromisoformat(prev_update_iso)
    except ValueError:
        return 0.0
    now = datetime.now(UTC)
    delta_hours = (now - prev).total_seconds() / 3600
    # cap to a sane window (skip if no recent update e.g. fresh state)
    if delta_hours <= 0 or delta_hours > 24:
        return 0.0
    fraction_of_8h = delta_hours / 8.0
    total = 0.0
    for sym, qty in state["positions"].items():
        if abs(qty) < 1e-10:
            continue
        price = state["prices"].get(sym, 0)
        if price <= 0:
            continue
        rate_per_8h = _funding_rate_for(sym)
        # long pays positive funding; short receives. notional × rate × frac.
        total += qty * price * rate_per_8h * fraction_of_8h
    return total


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


def update(state: dict, cfg=None) -> dict:
    """Process latest signals, update portfolio.

    Guard logic:
      - signal has `parked: true`  → force target_position = 0 (liquidate)
      - signal missing              → hold existing position (skip update)
    """
    signals = get_latest_signals()
    if not signals:
        print("No signals found.")
        return state

    symbols = _all_tracked_symbols(cfg) if cfg is not None else list(state["positions"].keys())
    ts = datetime.now(UTC).isoformat()
    bar_pnl = 0.0
    bar_costs = 0.0
    trades = []
    parked_liquidations = []

    for sym in symbols:
        sig = signals.get(sym)
        if not sig:
            continue

        new_price = sig.get("price", 0)
        old_price = state["prices"].get(sym, 0)
        old_pos = state["positions"].get(sym, 0.0)

        # Respect live guard + config park: parked → force flat
        if sig.get("parked", False):
            new_pos = 0.0
            if abs(old_pos) > 1e-6:
                parked_liquidations.append(f"{sym}({sig.get('live_guard', 'PARKED')})")
        else:
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
        if new_price > 0:
            state["prices"][sym] = new_price

    # Funding cost (opt-in via PAPER_SIM_FUNDING env). Charged based on
    # positions BEFORE the bar's trade — i.e. what was held since prev bar.
    bar_funding = 0.0
    if SIM_FUNDING:
        bar_funding = _bar_funding_cost(state, state.get("last_update"))
        state.setdefault("total_funding", 0.0)
        state["total_funding"] += bar_funding

    # Update capital
    net_pnl = bar_pnl - bar_costs - bar_funding
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
        "prices": {k: round(v, 6) for k, v in state["prices"].items() if v > 0},
        "trades": trades,
    }
    if SIM_FUNDING:
        record["funding"] = round(bar_funding, 4)
    append_history(record)

    # Print summary
    ret_pct = (state["capital"] / INITIAL_CAPITAL - 1) * 100
    print(f"  Updated: capital=${state['capital']:,.2f} ({ret_pct:+.1f}%)")
    funding_note = f"  funding=${bar_funding:+.4f}" if SIM_FUNDING else ""
    print(f"  Bar PnL=${net_pnl:+.2f}  costs=${bar_costs:.2f}{funding_note}")
    if parked_liquidations:
        print(f"  🔒 Parked liquidations: {', '.join(parked_liquidations)}")
    if trades:
        print(f"  Trades: {', '.join(trades)}")
    print(f"  DD={state['max_drawdown']:.1%}  total_trades={state['n_trades']}")

    return state


def status(state: dict, cfg=None):
    symbols = _all_tracked_symbols(cfg) if cfg is not None else list(state["positions"].keys())
    parked_set = set(cfg.symbols_parked.keys()) if cfg is not None else set()

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
    for sym in symbols:
        pos = state["positions"].get(sym, 0)
        price = state["prices"].get(sym, 0)
        notional = abs(pos) * state["capital"]
        tag = " 🔒" if sym in parked_set else ""
        if abs(pos) > 0.01:
            print(f"    {sym:10s} pos={pos:+.3f}  price=${price:>10,.2f}  notional=${notional:>10,.2f}{tag}")
        else:
            print(f"    {sym:10s} FLAT{tag}")
    net_exp = sum(state["positions"].get(s, 0) for s in symbols)
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

    cfg = None
    if CONFIG_PATH.exists():
        try:
            cfg = load_config(CONFIG_PATH)
        except Exception as exc:
            print(f"WARN: failed to load config ({exc}); running without parked-symbol guard.")

    state = load_state(cfg)

    if args.command == "update":
        state = update(state, cfg)
        save_state(state)
    elif args.command == "status":
        status(state, cfg)
    elif args.command == "history":
        history()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
