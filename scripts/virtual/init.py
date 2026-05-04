#!/usr/bin/env python3
"""Initialize the virtual futures state with a given equity.

Idempotent if state doesn't exist. If state exists, refuses to run unless
--force is given (so accidental re-runs don't wipe simulated PnL).

Usage:
    python3 scripts/virtual/init.py --equity 10000
    python3 scripts/virtual/init.py --equity 50000 --force
"""
from __future__ import annotations

import argparse
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
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing virtual state (destroys history+PnL)")
    args = ap.parse_args()

    if VIRTUAL_STATE_FILE.exists() and not args.force:
        print(f"Refusing to overwrite existing state at {VIRTUAL_STATE_FILE}")
        print("  Use --force to wipe and start over, or run scripts/virtual/status.py.")
        return 1

    c = VirtualFuturesConnector(initial_equity=args.equity, reset=True)
    snap = c.snapshot()
    print(f"Virtual futures state initialized.")
    print(f"  equity: ${snap['equity']:,.2f}")
    print(f"  state file: {VIRTUAL_STATE_FILE}")
    print(f"  isolation marker: {VIRTUAL_STATE_FILE.parent / 'IS_VIRTUAL_NOT_REAL.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
