#!/usr/bin/env python3
"""Wipe virtual state (positions, PnL, history) and restart.

Prompts for "yes" before wiping unless --yes is given.

Usage:
    python3 scripts/virtual/reset.py --equity 10000
    python3 scripts/virtual/reset.py --equity 10000 --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.execution.virtual_futures import (  # noqa: E402
    VIRTUAL_HISTORY_FILE,
    VIRTUAL_STATE_FILE,
    VirtualFuturesConnector,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    ap.add_argument("--archive", action="store_true",
                    help="before wiping, move history.jsonl to history_archived_<ts>.jsonl")
    args = ap.parse_args()

    if not VIRTUAL_STATE_FILE.exists():
        print("No virtual state to reset. Use scripts/virtual/init.py.")
        return 1

    if not args.yes:
        print(f"About to WIPE virtual state at {VIRTUAL_STATE_FILE.parent}")
        print(f"  Current state + history will be LOST.")
        resp = input("Type 'yes' to confirm: ").strip().lower()
        if resp != "yes":
            print("Aborted.")
            return 2

    if args.archive and VIRTUAL_HISTORY_FILE.exists():
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_path = VIRTUAL_HISTORY_FILE.parent / f"history_archived_{stamp}.jsonl"
        VIRTUAL_HISTORY_FILE.rename(archive_path)
        print(f"  archived previous history → {archive_path}")

    c = VirtualFuturesConnector(initial_equity=args.equity, reset=True)
    print(f"Reset to equity ${args.equity:,.2f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
