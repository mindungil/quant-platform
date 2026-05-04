#!/usr/bin/env python3
"""Show the current virtual futures account status.

Reads data/virtual/state.json and fetches live marks so unrealized PnL
is up-to-date. Read-only — never mutates state.

Usage:
    python3 scripts/virtual/status.py
    python3 scripts/virtual/status.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.execution.virtual_futures import (  # noqa: E402
    VIRTUAL_STATE_FILE,
    VirtualFuturesConnector,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not VIRTUAL_STATE_FILE.exists():
        print("No virtual state found. Run scripts/virtual/init.py first.")
        return 1

    c = VirtualFuturesConnector(reset=False)
    snap = c.snapshot()

    if args.json:
        print(json.dumps(snap, indent=2, default=str))
        return 0

    equity = snap["equity"]
    balance = snap["balance"]
    realized = snap["realized_pnl"]
    unrealized = snap["unrealized_pnl"]
    fees = snap["total_fees"]
    initial = equity - realized - unrealized + fees  # approximate starting equity
    ret_pct = (equity / initial - 1) * 100 if initial > 0 else 0.0

    print("=" * 56)
    print("  VIRTUAL FUTURES STATUS")
    print("=" * 56)
    print(f"  Equity:      ${equity:>14,.2f}  ({ret_pct:+.2f}% from start)")
    print(f"  Balance:     ${balance:>14,.2f}")
    print(f"  Realized:    ${realized:>+14,.2f}")
    print(f"  Unrealized:  ${unrealized:>+14,.2f}")
    print(f"  Total fees:  ${fees:>14,.2f}")
    print(f"  Initialized: {snap['initialized_at']}")
    print(f"  Last update: {snap['last_update']}")
    print(f"  Counters:    orders={snap['n_orders']}  fills={snap['n_fills']}  "
          f"rejected={snap['n_rejected']}  open={len(snap['open_orders'])}")
    print()
    positions = snap["positions"]
    if positions:
        print("  Positions:")
        for sym, q in sorted(positions.items()):
            if abs(q) < 1e-10:
                continue
            avg = snap["avg_entry_prices"].get(sym, 0)
            mark = c._fetch_mark_prices_raw([sym]).get(sym, 0)
            upl = q * (mark - avg) if avg > 0 and mark > 0 else 0
            print(f"    {sym:10s} qty={q:+.6f}  avg=${avg:,.2f}  mark=${mark:,.2f}  UPL=${upl:+,.2f}")
    else:
        print("  Positions: (none)")

    if snap["open_orders"]:
        print("\n  Open orders:")
        for o in snap["open_orders"]:
            print(f"    {o['side']} {o['symbol']} qty={o['quantity']} price=${o['price']:,.2f}")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
