#!/usr/bin/env python3
"""Execution quality dashboard.

Reads every JSONL file under data/logs/ledger and aggregates:

  - total_orders, total_notional (per symbol, per side)
  - fill rate  = filled_qty / target_qty
  - avg slippage bps vs order price (where recorded)
  - rejected / errored count
  - avg latency (if ts_epoch on order + result recorded)
  - predicted vs actual impact ratio (from drift monitor log)

Output: pretty-printed table + JSON to stdout. Optional --out writes JSON
to disk for the frontend to render.

Usage:
    python3 scripts/ops/exec_quality_report.py [--days 7] [--out …]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


LEDGER_DIR = Path(REPO_ROOT) / "data" / "logs" / "ledger"
DRIFT_LOG = Path(REPO_ROOT) / "data" / "logs" / "drift_monitor.jsonl"


def _iter_ledger_records(days: int) -> list[dict]:
    if not LEDGER_DIR.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    for f in sorted(LEDGER_DIR.glob("exec_*.jsonl")):
        # exec_YYYYMMDD.jsonl — filter by name first for speed
        try:
            dt = datetime.strptime(f.stem.split("_")[1], "%Y%m%d").replace(tzinfo=timezone.utc)
            if dt < cutoff - timedelta(days=1):
                continue
        except Exception:
            pass
        with open(f) as fp:
            for line in fp:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts_epoch")
                if ts and datetime.fromtimestamp(ts, tz=timezone.utc) < cutoff:
                    continue
                out.append(rec)
    return out


def _iter_drift_records(days: int) -> list[dict]:
    if not DRIFT_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    with open(DRIFT_LOG) as fp:
        for line in fp:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = rec.get("ts")
            if ts and datetime.fromtimestamp(ts, tz=timezone.utc) < cutoff:
                continue
            out.append(rec)
    return out


def aggregate(days: int = 7) -> dict:
    records = _iter_ledger_records(days)
    if not records:
        return {
            "days": days,
            "total_orders": 0,
            "note": "no ledger records in window",
        }

    per_symbol: dict[str, dict] = defaultdict(lambda: {
        "orders": 0,
        "filled": 0,
        "rejected": 0,
        "errored": 0,
        "buy_notional": 0.0,
        "sell_notional": 0.0,
        "total_qty_target": 0.0,
        "total_qty_filled": 0.0,
        "slippage_bps_sum": 0.0,
        "slippage_bps_n": 0,
    })

    for rec in records:
        order = rec.get("order", {})
        symbol = order.get("symbol", "?")
        side = (order.get("side") or "").upper()
        qty_target = float(order.get("quantity", 0.0))
        price_ref = float(order.get("price") or 0.0)

        s = per_symbol[symbol]
        s["orders"] += 1
        s["total_qty_target"] += qty_target

        result = rec.get("result") or {}
        status = (result.get("status") or "").upper()
        filled_qty = float(result.get("filled_quantity", 0.0))
        avg_price = float(result.get("avg_price") or 0.0)

        if status in ("FILLED", "PARTIALLY_FILLED"):
            s["filled"] += 1
        elif status == "REJECTED":
            s["rejected"] += 1
        elif status in ("ERROR",):
            s["errored"] += 1

        s["total_qty_filled"] += filled_qty
        notional = filled_qty * avg_price
        if side == "BUY":
            s["buy_notional"] += notional
        elif side == "SELL":
            s["sell_notional"] += notional

        # Slippage if we have both avg_price and a reference price_ref
        if price_ref > 0 and avg_price > 0:
            if side == "BUY":
                slip = (avg_price - price_ref) / price_ref * 1e4
            else:
                slip = (price_ref - avg_price) / price_ref * 1e4
            s["slippage_bps_sum"] += float(slip)
            s["slippage_bps_n"] += 1

    summary: dict[str, dict] = {}
    for sym, s in per_symbol.items():
        fill_rate = s["total_qty_filled"] / s["total_qty_target"] if s["total_qty_target"] > 0 else 0.0
        avg_slip = s["slippage_bps_sum"] / s["slippage_bps_n"] if s["slippage_bps_n"] > 0 else None
        summary[sym] = {
            "orders": s["orders"],
            "filled": s["filled"],
            "rejected": s["rejected"],
            "errored": s["errored"],
            "fill_rate": round(fill_rate, 4),
            "buy_notional": round(s["buy_notional"], 2),
            "sell_notional": round(s["sell_notional"], 2),
            "avg_slippage_bps": round(avg_slip, 2) if avg_slip is not None else None,
        }

    drift = _iter_drift_records(days)
    drift_summary = None
    if drift:
        last = drift[-1]
        drift_summary = {
            "fills_tracked": len(drift),
            "last_ratio": last.get("ratio"),
            "last_level": last.get("level"),
        }

    return {
        "days": days,
        "total_orders": sum(s["orders"] for s in per_symbol.values()),
        "per_symbol": summary,
        "drift": drift_summary,
    }


def _print_table(summary: dict) -> None:
    if summary.get("total_orders", 0) == 0:
        print(f"(no ledger records in last {summary.get('days')} days)")
        return
    print(f"Execution quality (last {summary['days']} days)")
    print(f"  total orders: {summary['total_orders']}")
    print(f"  {'SYMBOL':<12} {'ORDERS':>6} {'FILL%':>7} {'SLIP(bps)':>10} {'BUY':>14} {'SELL':>14}")
    for sym, s in summary["per_symbol"].items():
        slip = f"{s['avg_slippage_bps']:.2f}" if s["avg_slippage_bps"] is not None else "  —"
        print(
            f"  {sym:<12} {s['orders']:>6} {s['fill_rate'] * 100:>6.1f}% "
            f"{slip:>10} {s['buy_notional']:>14,.0f} {s['sell_notional']:>14,.0f}"
        )
    if summary.get("drift"):
        print(f"  drift: {summary['drift']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    summary = aggregate(days=args.days)
    _print_table(summary)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
