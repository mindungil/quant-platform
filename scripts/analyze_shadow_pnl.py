#!/usr/bin/env python3
"""D21 — Per-strategy cumulative PnL validator.

Reads shadow_fills and reports the realized PnL distribution per
strategy_id. Designed to sanity-check that the closed loop (decision →
fill → recorder.pnl → MAB) actually produces a useful PnL signal across
strategies, not just one or two outliers.

Usage:
    python scripts/analyze_shadow_pnl.py            # last 24h
    python scripts/analyze_shadow_pnl.py --hours 6  # custom window
    python scripts/analyze_shadow_pnl.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys


def _connect():
    import psycopg
    url = os.getenv("POSTGRES_URL",
                    "postgresql+psycopg://postgres:postgres@db:5432/platform")
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url, autocommit=True, connect_timeout=5)


def _aggregate(hours: int) -> list[dict]:
    sql = """
    SELECT strategy_id,
           COUNT(*) AS fills,
           COUNT(*) FILTER (WHERE pnl IS NOT NULL AND pnl != 0) AS nz_pnl_fills,
           COALESCE(SUM(pnl), 0)::float AS cum_pnl,
           COALESCE(AVG(pnl), 0)::float AS mean_pnl,
           COALESCE(STDDEV(pnl), 0)::float AS std_pnl,
           COUNT(*) FILTER (WHERE pnl > 0) AS wins,
           COUNT(*) FILTER (WHERE pnl < 0) AS losses,
           MIN(ts) AS first_fill,
           MAX(ts) AS last_fill
    FROM shadow_fills
    WHERE ts > NOW() - (%s || ' hours')::interval
    GROUP BY strategy_id
    HAVING COUNT(*) > 0
    ORDER BY cum_pnl DESC
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (str(hours),))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _per_strategy_sharpe(rows: list[dict]) -> None:
    """Decorate each row with naive Sharpe (mean / std * sqrt(N))."""
    for r in rows:
        n = r["fills"]
        std = r["std_pnl"] or 0.0
        if n >= 5 and std > 1e-12:
            r["naive_sharpe"] = round(r["mean_pnl"] / std * math.sqrt(n), 4)
        else:
            r["naive_sharpe"] = 0.0
        wins, losses = r["wins"], r["losses"]
        decided = wins + losses
        r["win_rate"] = round(wins / decided, 4) if decided else 0.0


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no fills in window)")
        return
    # Header
    print(f"{'strategy_id':<40} {'fills':>6} {'nz':>4} "
          f"{'cum_pnl':>14} {'mean':>12} {'std':>12} "
          f"{'win%':>6} {'naive_SR':>9}")
    print("-" * 120)
    total_cum = 0.0
    for r in rows:
        sid = r["strategy_id"]
        sid_disp = sid if len(sid) <= 40 else sid[:37] + "..."
        print(f"{sid_disp:<40} {r['fills']:>6} {r['nz_pnl_fills']:>4} "
              f"{r['cum_pnl']:>14.6f} {r['mean_pnl']:>12.6f} {r['std_pnl']:>12.6f} "
              f"{r['win_rate']*100:>5.1f}% {r['naive_sharpe']:>9.4f}")
        total_cum += r["cum_pnl"]
    print("-" * 120)
    print(f"{'TOTAL':<40} {'':>6} {'':>4} {total_cum:>14.6f}")
    # Highlight zero-pnl strategies (loop didn't deliver real reward signal)
    zero_signal = [r for r in rows if r["nz_pnl_fills"] == 0]
    if zero_signal:
        print()
        print(f"⚠ {len(zero_signal)} strategy(s) with zero non-zero pnl fills "
              f"(MAB receives only 0-reward updates from these)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=int, default=24,
                   help="window in hours (default: 24)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of table")
    args = p.parse_args()
    try:
        rows = _aggregate(args.hours)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Serialize datetimes for both table and json paths
    for r in rows:
        for k in ("first_fill", "last_fill"):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
    _per_strategy_sharpe(rows)
    if args.json:
        print(json.dumps({"hours": args.hours, "strategies": rows}, indent=2))
    else:
        print(f"window: last {args.hours}h, strategies: {len(rows)}")
        _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
