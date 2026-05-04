#!/usr/bin/env python3
"""Cost / slippage analysis — live (or virtual) execution vs backtest assumptions.

Reads fill-level execution history and reports:

  * fee distribution (mean/median/max bps), maker vs taker mix
  * realized slippage when limit_price is present
    slippage_bps = sign(side) * (fill_price − limit_price) / limit_price * 1e4
  * funding (when fills carry it; otherwise N/A)
  * daily aggregate cost ($) and bps-of-notional
  * deviation vs backtest assumptions (DEFAULT_TAKER_BPS=5,
    DEFAULT_MAKER_BPS=2 from shared/execution/virtual_futures.py)

Sources:
  --source virtual   data/virtual/history.jsonl     (default)
  --source live      data/logs/execution/*.jsonl
  --source paper     data/paper/portfolio_history.jsonl  (limited — only aggregates)

Usage:
  python3 scripts/live/cost_analysis.py
  python3 scripts/live/cost_analysis.py --source live --days 7
  python3 scripts/live/cost_analysis.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone, date as date_cls, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

VIRTUAL_HISTORY = REPO_ROOT / "data" / "virtual" / "history.jsonl"
PAPER_HISTORY = REPO_ROOT / "data" / "paper" / "portfolio_history.jsonl"
LIVE_LOG_DIR = REPO_ROOT / "data" / "logs" / "execution"

# Backtest cost assumptions (from shared/execution/virtual_futures.py)
BACKTEST_MAKER_BPS = 2.0
BACKTEST_TAKER_BPS = 5.0


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _load_fills(source: str, since: datetime) -> list[dict]:
    """Normalize fill records from each source into a common shape:
      {ts, symbol, side, fill_price, limit_price, qty, fee, fee_bps, is_maker}
    """
    out: list[dict] = []
    if source == "virtual":
        for r in _read_jsonl(VIRTUAL_HISTORY):
            if r.get("type") != "fill":
                continue
            try:
                ts = _parse_iso(r["timestamp"])
            except (KeyError, ValueError):
                continue
            if ts < since:
                continue
            out.append({
                "ts": ts,
                "symbol": r.get("symbol"),
                "side": r.get("side"),
                "fill_price": r.get("fill_price"),
                "limit_price": r.get("limit_price"),
                "qty": r.get("filled_qty"),
                "fee": r.get("fee"),
                "fee_bps": r.get("fee_bps"),
                "is_maker": bool(r.get("is_maker", False)),
            })
    elif source == "live":
        if not LIVE_LOG_DIR.exists():
            return []
        for fpath in sorted(LIVE_LOG_DIR.glob("*.jsonl")):
            for r in _read_jsonl(fpath):
                # Live log shape may vary by exchange; pick common fields
                if "fill_price" not in r and "fillPrice" not in r:
                    continue
                ts_raw = r.get("ts") or r.get("timestamp")
                try:
                    ts = _parse_iso(ts_raw) if ts_raw else None
                except ValueError:
                    ts = None
                if ts is None or ts < since:
                    continue
                out.append({
                    "ts": ts,
                    "symbol": r.get("symbol"),
                    "side": r.get("side"),
                    "fill_price": r.get("fill_price") or r.get("fillPrice"),
                    "limit_price": r.get("limit_price") or r.get("limitPrice"),
                    "qty": r.get("qty") or r.get("filledQty"),
                    "fee": r.get("fee") or r.get("commission"),
                    "fee_bps": r.get("fee_bps"),
                    "is_maker": bool(r.get("is_maker") or r.get("maker", False)),
                })
    elif source == "paper":
        # Paper history's `trades` field is a list of human-readable
        # strings ("BNBUSDT -0.007→+0.113") with no fee/qty/price detail
        # — paper portfolio bypasses fill-level accounting. Surface this
        # as no fills rather than crashing; operator should use --source
        # virtual or live for cost analysis.
        return []
    else:
        raise ValueError(f"unknown source: {source}")
    return out


def _slippage_bps(fill: dict) -> float | None:
    """Realized slippage: positive = adverse (fill worse than limit)."""
    lp = fill.get("limit_price")
    fp = fill.get("fill_price")
    side = fill.get("side")
    if lp is None or fp is None or not side:
        return None
    if lp <= 0:
        return None
    sign = 1 if side.upper() in ("BUY", "LONG") else -1
    return sign * (fp - lp) / lp * 1e4


def analyze(fills: list[dict]) -> dict:
    n = len(fills)
    if n == 0:
        return {"n_fills": 0}

    fee_bps = [f["fee_bps"] for f in fills if isinstance(f.get("fee_bps"), (int, float))]
    fee_usd = [f["fee"] for f in fills if isinstance(f.get("fee"), (int, float))]
    notional = [
        abs((f.get("qty") or 0) * (f.get("fill_price") or 0))
        for f in fills
    ]
    n_maker = sum(1 for f in fills if f.get("is_maker"))
    n_taker = n - n_maker

    slips = [s for s in (_slippage_bps(f) for f in fills) if s is not None]

    # Daily aggregation
    by_day_fee: dict[str, float] = defaultdict(float)
    by_day_notional: dict[str, float] = defaultdict(float)
    by_day_count: dict[str, int] = defaultdict(int)
    for f in fills:
        d = f["ts"].date().isoformat()
        if isinstance(f.get("fee"), (int, float)):
            by_day_fee[d] += float(f["fee"])
        ntl = abs((f.get("qty") or 0) * (f.get("fill_price") or 0))
        by_day_notional[d] += ntl
        by_day_count[d] += 1

    # Per-symbol
    by_sym: dict[str, dict] = defaultdict(lambda: {"n": 0, "fee_usd": 0.0, "notional": 0.0})
    for f in fills:
        s = f.get("symbol", "?")
        by_sym[s]["n"] += 1
        if isinstance(f.get("fee"), (int, float)):
            by_sym[s]["fee_usd"] += float(f["fee"])
        by_sym[s]["notional"] += abs((f.get("qty") or 0) * (f.get("fill_price") or 0))

    expected_avg_bps = (n_maker * BACKTEST_MAKER_BPS + n_taker * BACKTEST_TAKER_BPS) / n if n else 0
    realized_avg_bps = statistics.mean(fee_bps) if fee_bps else 0.0
    deviation_bps = realized_avg_bps - expected_avg_bps

    return {
        "n_fills": n,
        "n_maker": n_maker,
        "n_taker": n_taker,
        "maker_ratio": (n_maker / n) if n else 0,
        "fee_bps": {
            "mean": realized_avg_bps,
            "median": statistics.median(fee_bps) if fee_bps else None,
            "max": max(fee_bps) if fee_bps else None,
            "expected_mix_avg": expected_avg_bps,
            "deviation_vs_expected": deviation_bps,
        },
        "fee_usd_total": sum(fee_usd) if fee_usd else 0.0,
        "notional_total": sum(notional),
        "fee_pct_of_notional": (sum(fee_usd) / sum(notional)) if (fee_usd and sum(notional) > 0) else 0,
        "slippage_bps": {
            "n": len(slips),
            "mean": statistics.mean(slips) if slips else None,
            "median": statistics.median(slips) if slips else None,
            "max_adverse": max(slips) if slips else None,
            "min_favorable": min(slips) if slips else None,
        } if slips else {"n": 0, "note": "no limit-priced fills in window"},
        "by_day": [
            {"date": d, "fee_usd": by_day_fee[d], "notional_usd": by_day_notional[d],
             "n_fills": by_day_count[d]}
            for d in sorted(by_day_count.keys())
        ],
        "by_symbol": [
            {"symbol": s, "n_fills": v["n"], "fee_usd": v["fee_usd"],
             "notional_usd": v["notional"]}
            for s, v in sorted(by_sym.items(), key=lambda kv: -kv[1]["fee_usd"])
        ],
    }


def render_text(report: dict, source: str, days: int) -> str:
    if not report.get("n_fills"):
        return f"# Cost analysis ({source}, last {days}d): no fills in window"
    lines = [
        f"# Cost analysis — source={source}, last {days}d",
        f"  fills: {report['n_fills']}  maker:{report['n_maker']}  taker:{report['n_taker']}  "
        f"maker_ratio: {report['maker_ratio']:.1%}",
        "",
        "## Fees",
        f"  realized avg: {report['fee_bps']['mean']:.2f} bps  median: "
        f"{report['fee_bps'].get('median', 'n/a')!s}  max: {report['fee_bps'].get('max', 'n/a')!s}",
        f"  backtest mix avg (maker={BACKTEST_MAKER_BPS}/taker={BACKTEST_TAKER_BPS}): "
        f"{report['fee_bps']['expected_mix_avg']:.2f} bps",
        f"  deviation vs expected: {report['fee_bps']['deviation_vs_expected']:+.2f} bps",
        f"  total fees: ${report['fee_usd_total']:,.4f}  notional: "
        f"${report['notional_total']:,.0f}  ({report['fee_pct_of_notional']*1e4:.2f} bps)",
    ]
    sl = report["slippage_bps"]
    lines += ["", "## Slippage (limit-priced fills only)"]
    if sl.get("n", 0) > 0:
        lines.append(f"  n={sl['n']}  mean={sl['mean']:.2f}  median={sl['median']:.2f}  "
                     f"max_adverse={sl['max_adverse']:.2f}  min_favorable={sl['min_favorable']:.2f}")
    else:
        lines.append(f"  {sl.get('note', 'n=0')}")
    if report.get("by_symbol"):
        lines += ["", "## By symbol"]
        lines.append(f"  {'symbol':<10} {'fills':>6} {'fee_$':>10} {'notional_$':>12} {'fee_bps':>9}")
        for row in report["by_symbol"]:
            bps = (row["fee_usd"] / row["notional_usd"] * 1e4) if row["notional_usd"] > 0 else 0
            lines.append(
                f"  {row['symbol']:<10} {row['n_fills']:>6} "
                f"{row['fee_usd']:>10.4f} {row['notional_usd']:>12.2f} {bps:>9.2f}"
            )
    if report.get("by_day"):
        lines += ["", "## By day"]
        for row in report["by_day"]:
            bps = (row["fee_usd"] / row["notional_usd"] * 1e4) if row["notional_usd"] > 0 else 0
            lines.append(
                f"  {row['date']}: fills={row['n_fills']:>3}  fee=${row['fee_usd']:.4f}  "
                f"notional=${row['notional_usd']:,.0f}  ({bps:.2f} bps)"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cost / slippage analysis")
    parser.add_argument("--source", choices=["virtual", "paper", "live"], default="virtual")
    parser.add_argument("--days", type=int, default=7,
                        help="Lookback window in days (default 7)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    fills = _load_fills(args.source, since)
    report = analyze(fills)

    if args.json:
        # Convert datetime fields in by_day are already strings; report is JSON-safe
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(report, args.source, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
