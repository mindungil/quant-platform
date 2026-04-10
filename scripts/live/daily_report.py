#!/usr/bin/env python3
"""Daily performance report.

Reads accumulated signal JSONs from data/signals/, reconstructs the
paper-trading PnL, and prints a daily summary. Run once a day via cron.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SIGNALS_DIR = REPO_ROOT / "data" / "signals"
UTC = timezone.utc


def load_signals() -> list[dict]:
    """Load all signal JSONs, sorted by time."""
    files = sorted(SIGNALS_DIR.glob("signals_*.json"))
    all_sigs = []
    for f in files:
        try:
            with open(f) as fh:
                sigs = json.load(fh)
                ts = f.stem.replace("signals_", "")
                for s in sigs:
                    s["_file_ts"] = ts
                all_sigs.extend(sigs)
        except Exception:
            pass
    return all_sigs


def main() -> int:
    sigs = load_signals()
    if not sigs:
        print("No signal files found. Run generate_signals.py first.")
        return 1

    # Group by file timestamp
    by_ts = {}
    for s in sigs:
        ts = s.get("_file_ts", "?")
        by_ts.setdefault(ts, []).append(s)

    n_snapshots = len(by_ts)
    symbols = sorted({s["symbol"] for s in sigs if "symbol" in s and "error" not in s})

    print(f"\n{'='*60}")
    print(f"  DAILY REPORT — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  {n_snapshots} signal snapshots, {len(symbols)} symbols")
    print(f"{'='*60}")

    # Latest snapshot
    latest_ts = sorted(by_ts.keys())[-1]
    latest = by_ts[latest_ts]
    print(f"\n  Latest ({latest_ts}):")
    for s in latest:
        if "error" in s:
            continue
        pos = s.get("target_position", 0)
        sh = s.get("rolling_30d_sharpe", 0)
        print(f"    {s['symbol']:10s} pos={pos:+.3f}  30d_sh={sh:+.2f}  price=${s.get('price', 0):,.2f}")

    # Position history per symbol
    print(f"\n  Position history:")
    for sym in symbols:
        sym_sigs = [s for s in sigs if s.get("symbol") == sym and "error" not in s]
        positions = [s.get("target_position", 0) for s in sym_sigs]
        if positions:
            print(f"    {sym:10s} mean={np.mean(positions):+.3f}  min={min(positions):+.3f}  max={max(positions):+.3f}  n={len(positions)}")

    print(f"\n{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
