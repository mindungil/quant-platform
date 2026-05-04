#!/usr/bin/env python3
"""Compare paper_portfolio and virtual-futures P&L series.

Two independent simulators both consume the same signal JSONs. Their
output should track within a small error; sustained divergence means
one of them has drifted.

Paper portfolio (scripts/live/paper_portfolio.py):
  - Fills at signal price, flat 5bp taker fee, no symbol filters.

Virtual futures (shared.execution.virtual_futures):
  - Fills at Binance mark (or with slippage if enabled), VIP0 maker/taker,
    symbol filters, partial fills optional.

We read each system's history and plot (text) daily equity & daily delta.
Read-only: never mutates either system's state.

Usage:
    python3 scripts/virtual/compare_paper.py
    python3 scripts/virtual/compare_paper.py --days 14
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

PAPER_HIST = REPO_ROOT / "data" / "paper" / "portfolio_history.jsonl"
VIRT_HIST = REPO_ROOT / "data" / "virtual" / "history.jsonl"
VIRT_STATE = REPO_ROOT / "data" / "virtual" / "state.json"

UTC = timezone.utc


def load_paper_series():
    if not PAPER_HIST.exists():
        return []
    out = []
    with open(PAPER_HIST) as f:
        for ln in f:
            try:
                r = json.loads(ln)
                out.append((r["timestamp"], r.get("capital", 0.0)))
            except Exception:
                continue
    return out


def load_virtual_series():
    """Reconstruct equity timeline from the fill history."""
    if not VIRT_HIST.exists():
        return []
    out = []
    with open(VIRT_HIST) as f:
        for ln in f:
            try:
                r = json.loads(ln)
                if r.get("type") == "fill" and "equity_after" in r:
                    out.append((r["timestamp"], float(r["equity_after"])))
            except Exception:
                continue
    return out


def take_last_per_day(series):
    by_day: dict[str, tuple[str, float]] = {}
    for ts, eq in series:
        day = ts[:10]
        if day not in by_day or ts > by_day[day][0]:
            by_day[day] = (ts, eq)
    return [(day, eq) for day, (_, eq) in sorted(by_day.items())]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    paper = take_last_per_day(load_paper_series())
    virt = take_last_per_day(load_virtual_series())

    paper_days = {d: e for d, e in paper}
    virt_days = {d: e for d, e in virt}

    all_days = sorted(set(paper_days.keys()) | set(virt_days.keys()))[-args.days:]
    if not all_days:
        print("No history found yet. Run the bridge (paper+virtual) at least once.")
        return 1

    print("=" * 72)
    print("  PAPER vs VIRTUAL — daily equity (last-of-day)")
    print("=" * 72)
    print(f"  {'day':12s} {'paper':>12s} {'virtual':>12s} {'Δ':>10s} {'Δ%':>8s}")
    print(f"  {'-' * 12} {'-' * 12} {'-' * 12} {'-' * 10} {'-' * 8}")
    paper_start = next((e for d, e in paper if d in all_days), None)
    virt_start = next((e for d, e in virt if d in all_days), None)
    rows = []
    for d in all_days:
        p = paper_days.get(d)
        v = virt_days.get(d)
        if p is None or v is None:
            row = f"  {d:12s} {'-' if p is None else f'${p:>10,.2f}':>12s} {'-' if v is None else f'${v:>10,.2f}':>12s}"
            print(row)
            continue
        delta = v - p
        delta_pct = (v / p - 1) * 100 if p > 0 else 0
        print(f"  {d:12s} ${p:>10,.2f}  ${v:>10,.2f}  ${delta:>+8,.2f}  {delta_pct:>+6.2f}%")
        rows.append((p, v, delta, delta_pct))

    if rows:
        avg_pct = sum(r[3] for r in rows) / len(rows)
        max_abs_pct = max(abs(r[3]) for r in rows)
        print(f"\n  [cumulative] Avg daily Δ%: {avg_pct:+.2f}%  Max |Δ%|: {max_abs_pct:.2f}%")
        print(f"  (cumulative equity diverges when sims start at different dates —")
        print(f"   the next block is the fair comparison.)")

    # Daily-return alignment — the fair check.
    # Two sims that receive the same signals should have similar *daily returns*
    # regardless of when each started or how much baseline PnL they carry.
    print()
    print("  PAPER vs VIRTUAL — daily-return alignment (bar-by-bar behaviour)")
    print(f"  {'day':12s} {'paper_ret%':>11s} {'virt_ret%':>11s} {'Δret bps':>10s}")
    print(f"  {'-' * 12} {'-' * 11} {'-' * 11} {'-' * 10}")
    prev_p = prev_v = None
    ret_rows = []
    for d in all_days:
        p = paper_days.get(d)
        v = virt_days.get(d)
        if p is None or v is None:
            prev_p = prev_v = None
            continue
        if prev_p is not None and prev_v is not None and prev_p > 0 and prev_v > 0:
            p_ret = (p / prev_p - 1) * 100
            v_ret = (v / prev_v - 1) * 100
            diff_bps = (v_ret - p_ret) * 100  # pct → bps
            print(f"  {d:12s} {p_ret:>+10.3f}  {v_ret:>+10.3f}  {diff_bps:>+8.1f}")
            ret_rows.append((p_ret, v_ret, diff_bps))
        prev_p, prev_v = p, v

    if ret_rows:
        avg_diff = sum(r[2] for r in ret_rows) / len(ret_rows)
        max_abs_diff = max(abs(r[2]) for r in ret_rows)
        print(f"\n  [daily-ret] Avg Δret: {avg_diff:+.1f} bps  Max |Δret|: {max_abs_diff:.1f} bps")
        # Promotion criterion: |Δret| < 200 bps (2%) sustained
        if max_abs_diff > 200:
            print("  ⚠ WARNING: daily-return divergence exceeds 200 bps — sims behave differently.")
        elif max_abs_diff > 100:
            print("  ~ CAUTION: daily-return drift > 100 bps, monitor.")
        else:
            print("  ✓ Daily returns tracking within 100 bps — sims behave consistently.")
    else:
        print("\n  (need ≥2 days of overlapping paper+virtual history for return alignment.)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
