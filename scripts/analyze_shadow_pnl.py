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
    # D19: LEFT JOIN strategies so the report shows the human-readable name
    # next to (or instead of) the opaque UUID. Strategies live in the
    # registry, fills carry strategy_id only — join here keeps the script
    # self-contained.
    sql = """
    SELECT sf.strategy_id,
           COALESCE(s.name, '(unknown)') AS strategy_name,
           COUNT(*) AS fills,
           COUNT(*) FILTER (WHERE sf.pnl IS NOT NULL AND sf.pnl != 0) AS nz_pnl_fills,
           COALESCE(SUM(sf.pnl), 0)::float AS cum_pnl,
           COALESCE(AVG(sf.pnl), 0)::float AS mean_pnl,
           COALESCE(STDDEV(sf.pnl), 0)::float AS std_pnl,
           COUNT(*) FILTER (WHERE sf.pnl > 0) AS wins,
           COUNT(*) FILTER (WHERE sf.pnl < 0) AS losses,
           MIN(sf.ts) AS first_fill,
           MAX(sf.ts) AS last_fill
    FROM shadow_fills sf
    LEFT JOIN strategies s ON s.id = sf.strategy_id
    WHERE sf.ts > NOW() - (%s || ' hours')::interval
    GROUP BY sf.strategy_id, s.name
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
    # D19: show "name (uuid_prefix)" so the same name across different
    # strategy_ids is still distinguishable (3 of 4 active strategies share
    # the default "Crypto Momentum Bootstrap" name).
    print(f"{'strategy':<40} {'fills':>6} {'nz':>4} "
          f"{'cum_pnl':>14} {'mean':>12} {'std':>12} "
          f"{'win%':>6} {'naive_SR':>9}")
    print("-" * 120)
    total_cum = 0.0
    for r in rows:
        name = r.get("strategy_name") or "(unknown)"
        sid_short = (r["strategy_id"] or "")[:8]
        label = f"{name} ({sid_short})"
        label = label if len(label) <= 40 else label[:37] + "..."
        print(f"{label:<40} {r['fills']:>6} {r['nz_pnl_fills']:>4} "
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
