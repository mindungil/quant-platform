#!/usr/bin/env python3
"""Funding cost retroactive estimate — what would paper PnL look like if
funding were charged?

Paper portfolio (`paper_portfolio.py`) tracks trading fees (5bps taker)
but does NOT charge funding. Perp positions held across funding settle
points (every 8h on Binance USDT-M futures) accrue funding cost/income:

    funding_cost(t) = position_notional(t) × funding_rate(t)
    sign convention:
      long  + positive_funding → COST  (long pays short when funding > 0)
      short + positive_funding → INCOME

This script reads paper history + data/funding/{sym}_funding.csv and
estimates the cumulative funding adjustment over a given window. Paper
state is NOT modified — this is a what-if read.

When funding data is missing for the live window (data files only go
through 2026-04-19), we fall back to a per-symbol baseline mean — this
is honest about the gap and labels the estimate accordingly.

Usage:
  python3 scripts/live/funding_cost_estimate.py
  python3 scripts/live/funding_cost_estimate.py --since 2026-04-25
  python3 scripts/live/funding_cost_estimate.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

PAPER_HISTORY = REPO_ROOT / "data" / "paper" / "portfolio_history.jsonl"
FUNDING_DIR = REPO_ROOT / "data" / "funding"
SIGNALS_DIR = REPO_ROOT / "data" / "signals"

FUNDING_INTERVAL_HOURS = 8  # Binance perp settles 3x/day


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_paper_history(since: datetime) -> list[dict]:
    if not PAPER_HISTORY.exists():
        return []
    out = []
    for line in PAPER_HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            ts = _parse_iso(r["timestamp"])
            if ts >= since:
                r["_dt"] = ts
                out.append(r)
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return out


def _load_funding(symbol: str) -> pd.Series | None:
    path = FUNDING_DIR / f"{symbol}_funding.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    return df.set_index("timestamp")["fundingRate"].astype(float).sort_index()


def _funding_at(series: pd.Series | None, ts: datetime, fallback_mean: float) -> tuple[float, str]:
    """Return (rate, source). source ∈ {actual, fallback_mean, no_data}."""
    if series is None or series.empty:
        return fallback_mean, "fallback_mean"
    ts = pd.Timestamp(ts).tz_convert("UTC") if ts.tzinfo else pd.Timestamp(ts).tz_localize("UTC")
    # Find latest settle point at or before ts
    candidates = series[series.index <= ts]
    if candidates.empty:
        return fallback_mean, "fallback_mean"
    last_ts = candidates.index[-1]
    age = ts - last_ts
    # Beyond 24h gap → fall back to mean (data stale)
    if age > pd.Timedelta(hours=24):
        return fallback_mean, "fallback_mean"
    return float(candidates.iloc[-1]), "actual"


def _funding_settle_times(start: datetime, end: datetime) -> list[datetime]:
    """Binance funding settles at 00:00, 08:00, 16:00 UTC. Return all
    settle timestamps in [start, end].
    """
    out = []
    # Round start up to next settle hour
    t = start.replace(minute=0, second=0, microsecond=0)
    if t.hour % FUNDING_INTERVAL_HOURS != 0 or t < start:
        # advance to next 8-hour boundary
        next_hour = ((t.hour // FUNDING_INTERVAL_HOURS) + 1) * FUNDING_INTERVAL_HOURS
        days = next_hour // 24
        next_hour = next_hour % 24
        t = t.replace(hour=next_hour) + timedelta(days=days)
    while t <= end:
        out.append(t)
        t = t + timedelta(hours=FUNDING_INTERVAL_HOURS)
    return out


def _signal_price_near(ts: datetime, symbol: str) -> float:
    """Fallback price lookup when paper history record's `prices` is null.
    Reads the closest signal file's `price` field for the symbol.
    """
    if not SIGNALS_DIR.exists():
        return 0.0
    files = sorted(SIGNALS_DIR.glob("signals_*.json"))
    # Pick the file whose mtime is closest to ts
    best = None
    best_age = None
    for f in files:
        f_mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if f_mtime > ts:
            continue
        age = (ts - f_mtime).total_seconds()
        if best_age is None or age < best_age:
            best, best_age = f, age
    if best is None:
        return 0.0
    try:
        payload = json.loads(best.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict) and entry.get("symbol") == symbol:
                    return float(entry.get("price") or 0.0)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return 0.0


def _position_at(history: list[dict], ts: datetime, symbol: str) -> tuple[float, float]:
    """Return (position_qty, price) at the latest history record at or
    before ts. (0, 0) if none. Falls back to signal-file price if paper
    history's `prices` field is null (paper_portfolio.py doesn't always
    write prices into history records).
    """
    candidates = [r for r in history if r["_dt"] <= ts]
    if not candidates:
        return 0.0, 0.0
    last = candidates[-1]
    qty = float((last.get("positions") or {}).get(symbol, 0.0))
    prices = last.get("prices") or {}
    price = float(prices.get(symbol, 0.0)) if isinstance(prices, dict) else 0.0
    if price <= 0:
        price = _signal_price_near(ts, symbol)
    return qty, price


def evaluate(since: datetime, until: datetime) -> dict:
    history = _load_paper_history(since)
    if not history:
        return {"status": "no_history", "since": since.isoformat()}

    # Per symbol — load funding and compute baseline mean
    symbols = set()
    for r in history:
        symbols.update((r.get("positions") or {}).keys())
    funding_data: dict[str, pd.Series | None] = {}
    baseline_mean: dict[str, float] = {}
    for s in sorted(symbols):
        series = _load_funding(s)
        funding_data[s] = series
        baseline_mean[s] = float(series.mean()) if series is not None and not series.empty else 0.0

    settle_times = _funding_settle_times(since, until)
    if not settle_times:
        return {"status": "no_settle_points", "since": since.isoformat(), "until": until.isoformat()}

    # First record price for fallback notional
    by_symbol_cost: dict[str, float] = {}
    by_symbol_n_settles: dict[str, int] = {}
    by_symbol_data_quality: dict[str, dict] = {}
    cost_events: list[dict] = []

    for sym in sorted(symbols):
        cost = 0.0
        n_actual = 0
        n_fallback = 0
        for st in settle_times:
            qty, price = _position_at(history, st, sym)
            if abs(qty) < 1e-10 or price <= 0:
                continue
            rate, source = _funding_at(funding_data[sym], st, baseline_mean[sym])
            notional = qty * price
            # cost = notional × rate (long pays positive funding)
            event_cost = notional * rate
            cost += event_cost
            if source == "actual":
                n_actual += 1
            else:
                n_fallback += 1
            cost_events.append({
                "ts": st.isoformat(),
                "symbol": sym,
                "qty": qty,
                "price": price,
                "notional": notional,
                "rate": rate,
                "rate_source": source,
                "cost": event_cost,
            })
        by_symbol_cost[sym] = cost
        by_symbol_n_settles[sym] = n_actual + n_fallback
        by_symbol_data_quality[sym] = {
            "n_actual": n_actual,
            "n_fallback": n_fallback,
            "baseline_mean_per_8h": baseline_mean[sym],
            "baseline_annualized_pct": baseline_mean[sym] * 3 * 365 * 100,
        }

    total_funding_cost = sum(by_symbol_cost.values())

    # Compare to gross PnL change in window
    gross_first = history[0].get("capital", 0)
    gross_last = history[-1].get("capital", 0)
    gross_pnl = gross_last - gross_first

    return {
        "status": "ok",
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "n_history_records": len(history),
        "n_settle_points": len(settle_times),
        "by_symbol_cost": by_symbol_cost,
        "by_symbol_data_quality": by_symbol_data_quality,
        "total_funding_cost": total_funding_cost,
        "gross_paper_pnl": gross_pnl,
        "gross_paper_pnl_pct": (gross_pnl / gross_first * 100) if gross_first else 0,
        "funding_adjusted_pnl": gross_pnl - total_funding_cost,
        "funding_adjusted_pnl_pct": ((gross_pnl - total_funding_cost) / gross_first * 100)
            if gross_first else 0,
        "data_warning": (
            "funding data ends 2026-04-19; live window uses fallback baseline mean"
            if any(q["n_fallback"] > 0 for q in by_symbol_data_quality.values()) else None
        ),
    }


def render_text(report: dict) -> str:
    if report["status"] != "ok":
        return f"# Funding cost estimate — {report['status']}"
    w = report["window"]
    lines = [
        f"# Funding cost retroactive estimate — {w['since'][:10]} → {w['until'][:10]}",
        f"  history records: {report['n_history_records']}  funding settle points: {report['n_settle_points']}",
    ]
    if report.get("data_warning"):
        lines.append(f"  ⚠ {report['data_warning']}")
    lines += ["", "## Per-symbol funding cost"]
    lines.append(f"  {'symbol':<10} {'cost ($)':>12} {'n_actual':>9} {'n_fallback':>11} {'baseline_ann':>13}")
    for sym, cost in sorted(report["by_symbol_cost"].items(), key=lambda kv: -abs(kv[1])):
        q = report["by_symbol_data_quality"][sym]
        lines.append(
            f"  {sym:<10} {cost:>+12.4f} {q['n_actual']:>9} {q['n_fallback']:>11} "
            f"{q['baseline_annualized_pct']:>+12.2f}%"
        )
    lines += [
        "",
        "## Bottom line",
        f"  total funding cost: ${report['total_funding_cost']:+.4f}",
        f"  gross paper PnL:    ${report['gross_paper_pnl']:+.4f}  ({report['gross_paper_pnl_pct']:+.4f}%)",
        f"  funding-adjusted:   ${report['funding_adjusted_pnl']:+.4f}  ({report['funding_adjusted_pnl_pct']:+.4f}%)",
    ]
    if report['funding_adjusted_pnl'] < 0 < report['gross_paper_pnl']:
        lines.append("  ⚠ Gross PnL is positive but funding-adjusted is NEGATIVE — alpha not paying for funding")
    elif report['funding_adjusted_pnl'] > 0:
        lines.append("  ✓ Net of funding still positive")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Funding cost retroactive estimate")
    parser.add_argument("--since", help="UTC date (default: 2026-04-25 = v4.5 deploy)")
    parser.add_argument("--until", help="UTC date (default: now)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    since = (datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
             if args.since else datetime(2026, 4, 25, tzinfo=timezone.utc))
    until = (datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
             if args.until else datetime.now(timezone.utc))

    report = evaluate(since, until)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
