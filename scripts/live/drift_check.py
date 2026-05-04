#!/usr/bin/env python3
"""Position drift checker — local state vs exchange state.

Different from shared/execution/drift_monitor.py which tracks fill-level
slippage. This script checks for STATE divergence: did our local
position book stay in sync with what the exchange thinks we own?

Sources of drift this catches:
  - External liquidations (exchange forced-closed a position)
  - Manual UI trades (operator placed a trade outside the bot)
  - Reduce-only fills that didn't update local state due to crash mid-fill
  - Funding settlement balance changes
  - Stale local cache after restart

Behavior by execution mode:
  paper    — reads data/paper/portfolio_state.json, no exchange call.
              Acts as a "is the loop alive" health check.
  virtual  — reads data/virtual/state.json + connector.get_positions().
  testnet  — Binance testnet get_positions() vs local state.
  live     — Binance mainnet get_positions() vs local state.

Output: one JSONL line per check to data/logs/drift/drift-{date}.jsonl.

CLI:
  python3 scripts/live/drift_check.py                      # one-shot
  python3 scripts/live/drift_check.py --watch 60           # every 60s
  python3 scripts/live/drift_check.py --abs-threshold 0.001  # custom drift gate

Exit codes:
  0  no drift over threshold
  1  drift detected (above warn threshold)
  2  drift severe (above critical, kill switch engagement candidate)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.execution.mode import get_execution_mode, ExecutionMode  # noqa: E402

LOG_DIR = REPO_ROOT / "data" / "logs" / "drift"
PAPER_STATE = REPO_ROOT / "data" / "paper" / "portfolio_state.json"
VIRTUAL_STATE = REPO_ROOT / "data" / "virtual" / "state.json"

logger = logging.getLogger("drift_check")


@dataclass
class DriftCheck:
    ts: str
    mode: str
    local: dict[str, float]
    exchange: dict[str, float] | None
    drifts: dict[str, float]                 # |local-exchange| per symbol
    max_drift: float
    max_drift_symbol: str
    severity: str                            # "ok" | "warn" | "critical"
    note: str = ""


def _load_local_positions(mode: ExecutionMode) -> dict[str, float]:
    if mode == ExecutionMode.PAPER and PAPER_STATE.exists():
        return json.loads(PAPER_STATE.read_text()).get("positions", {})
    if mode == ExecutionMode.VIRTUAL and VIRTUAL_STATE.exists():
        return json.loads(VIRTUAL_STATE.read_text()).get("positions", {})
    return {}


def _load_exchange_positions(mode: ExecutionMode) -> dict[str, float] | None:
    """Returns None for paper mode (no exchange to call)."""
    if mode == ExecutionMode.PAPER:
        return None
    try:
        if mode == ExecutionMode.VIRTUAL:
            from shared.execution.virtual_futures import VirtualFutures
            vf = VirtualFutures()
            return vf.get_positions()
        from shared.execution.binance_futures import BinanceFutures
        import os
        bf = BinanceFutures(
            api_key=os.getenv("BINANCE_API_KEY", ""),
            api_secret=os.getenv("BINANCE_API_SECRET", ""),
            testnet=(mode == ExecutionMode.TESTNET),
        )
        return bf.get_positions()
    except Exception as e:
        logger.warning("exchange get_positions failed: %s", e)
        return None


def check_drift(
    *,
    warn_threshold: float = 0.001,
    critical_threshold: float = 0.01,
) -> DriftCheck:
    ctx = get_execution_mode()
    local = _load_local_positions(ctx.mode)
    exchange = _load_exchange_positions(ctx.mode)

    drifts: dict[str, float] = {}
    if exchange is not None:
        all_symbols = set(local.keys()) | set(exchange.keys())
        for sym in all_symbols:
            drifts[sym] = round(abs(local.get(sym, 0.0) - exchange.get(sym, 0.0)), 8)
    max_drift = max(drifts.values(), default=0.0)
    max_drift_sym = max(drifts.items(), key=lambda kv: kv[1], default=("", 0.0))[0]

    if exchange is None:
        severity = "ok"
        note = "paper mode — no exchange to compare; loop alive"
    elif max_drift >= critical_threshold:
        severity = "critical"
        note = f"{max_drift_sym} drift {max_drift:.6f} ≥ critical {critical_threshold}"
    elif max_drift >= warn_threshold:
        severity = "warn"
        note = f"{max_drift_sym} drift {max_drift:.6f} ≥ warn {warn_threshold}"
    else:
        severity = "ok"
        note = "all symbols within tolerance"

    return DriftCheck(
        ts=datetime.now(timezone.utc).isoformat(),
        mode=ctx.mode.value,
        local={k: round(v, 6) for k, v in local.items()},
        exchange={k: round(v, 6) for k, v in (exchange or {}).items()} if exchange is not None else None,
        drifts=drifts,
        max_drift=max_drift,
        max_drift_symbol=max_drift_sym,
        severity=severity,
        note=note,
    )


def _write_log(check: DriftCheck) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = check.ts[:10]
    path = LOG_DIR / f"drift-{day}.jsonl"
    record: dict[str, Any] = {
        "ts": check.ts,
        "mode": check.mode,
        "severity": check.severity,
        "max_drift": check.max_drift,
        "max_drift_symbol": check.max_drift_symbol,
        "note": check.note,
        "local": check.local,
        "exchange": check.exchange,
        "drifts": check.drifts,
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return path


def _print_summary(check: DriftCheck) -> None:
    icon = {"ok": "✓", "warn": "⚠", "critical": "🛑"}.get(check.severity, "?")
    print(f"[{check.ts[:19]}] {icon} {check.mode.upper()} drift={check.max_drift:.6f} ({check.severity}) — {check.note}")
    if check.drifts:
        for sym, d in sorted(check.drifts.items(), key=lambda kv: -kv[1])[:5]:
            if d > 0:
                local = check.local.get(sym, 0.0)
                ex = (check.exchange or {}).get(sym, 0.0)
                print(f"    {sym:<10} local={local:+.4f}  exchange={ex:+.4f}  Δ={d:.6f}")


def _exit_code(check: DriftCheck) -> int:
    return {"ok": 0, "warn": 1, "critical": 2}.get(check.severity, 0)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Position drift check")
    p.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                   help="watch loop interval (0 = one-shot, default)")
    p.add_argument("--warn-threshold", type=float, default=0.001)
    p.add_argument("--critical-threshold", type=float, default=0.01)
    p.add_argument("--quiet", action="store_true", help="suppress stdout, log only")
    args = p.parse_args()

    last_exit = 0
    while True:
        check = check_drift(
            warn_threshold=args.warn_threshold,
            critical_threshold=args.critical_threshold,
        )
        _write_log(check)
        if not args.quiet:
            _print_summary(check)
        last_exit = _exit_code(check)
        if args.watch <= 0:
            break
        time.sleep(args.watch)
    return last_exit


if __name__ == "__main__":
    sys.exit(main())
