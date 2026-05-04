#!/usr/bin/env python3
"""Hourly engine health check.

Reads the latest signal file + paper portfolio state and reports:
  - per-symbol live_guard verdict (ACTIVE / WARN / PARKED / CONFIG_PARKED)
  - 6M and 30d rolling Sharpe per symbol
  - paper portfolio equity, drawdown, recent PnL
  - alert flags for WARN / PARKED conditions

Runs hourly via cron; output flows to data/logs/health.log so
operators can tail it to see whether anything has auto-parked.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SIGNALS_DIR = REPO_ROOT / "data" / "signals"
PAPER_STATE = REPO_ROOT / "data" / "paper" / "portfolio_state.json"
HEALTH_LOG_DIR = REPO_ROOT / "data" / "metrics" / "health_log"
UTC = timezone.utc


def _latest_signal_file() -> Path | None:
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(SIGNALS_DIR.glob("signals_*.json"), reverse=True)
    return files[0] if files else None


def main() -> int:
    ts = datetime.now(UTC)
    print(f"[{ts.isoformat()}] engine health check")

    latest = _latest_signal_file()
    if latest is None:
        print("  ⚠ NO SIGNAL FILE — generate_signals.py may not be running")
        return 1

    age_min = (ts.timestamp() - latest.stat().st_mtime) / 60
    freshness = "✓" if age_min < 90 else ("⚠" if age_min < 180 else "✗")
    print(f"  signal file: {latest.name}  ({age_min:.0f} min old {freshness})")

    try:
        with open(latest) as f:
            signals = json.load(f)
    except Exception as exc:
        print(f"  ✗ failed to parse signals: {exc}")
        return 1

    warn_syms: list[str] = []
    park_syms: list[str] = []
    for s in signals:
        sym = s.get("symbol", "?")
        guard = s.get("live_guard", "?")
        sr_6m = s.get("live_6m_sharpe")
        sr_30d = s.get("rolling_30d_sharpe")
        tp = s.get("target_position", 0.0)
        status = {
            "ACTIVE": "✓",
            "WARN": "⚠",
            "PARKED": "🔒",
            "CONFIG_PARKED": "🔒",
            "INSUFFICIENT_DATA": "?",
            "DISABLED": "—",
        }.get(guard, "?")
        sr6_str = f"{sr_6m:+.2f}" if isinstance(sr_6m, (int, float)) else "  n/a"
        sr30_str = f"{sr_30d:+.2f}" if isinstance(sr_30d, (int, float)) else "  n/a"
        print(f"  {status} {sym:10s} {guard:17s} pos={tp:+.3f}  6M={sr6_str}  30d={sr30_str}")
        if guard == "WARN":
            warn_syms.append(sym)
        elif guard in ("PARKED", "CONFIG_PARKED"):
            park_syms.append(sym)

    # Paper portfolio snapshot
    if PAPER_STATE.exists():
        try:
            with open(PAPER_STATE) as f:
                ps = json.load(f)
            cap = ps.get("capital", 0.0)
            peak = ps.get("peak_equity", cap)
            dd = ps.get("max_drawdown", 0.0) * 100
            last = ps.get("last_update", "?")
            n_trades = ps.get("n_trades", 0)
            cur_dd = (peak - cap) / peak * 100 if peak > 0 else 0.0
            print(f"\n  paper: capital=${cap:,.2f}  peak=${peak:,.2f}  cur_dd={cur_dd:.2f}%  max_dd={dd:.2f}%")
            print(f"         n_trades={n_trades}  last_update={last}")
            if cur_dd > 15:
                print(f"  ⚠ paper drawdown > 15% — investigate")
        except Exception as exc:
            print(f"  ? paper state parse failed: {exc}")

    if warn_syms:
        print(f"\n  ⚠ WARN (half-size): {', '.join(warn_syms)}")
    if park_syms:
        print(f"  🔒 PARKED: {', '.join(park_syms)}")
    if not warn_syms and not park_syms:
        print("\n  ✓ all symbols ACTIVE")

    # Persist for later diffing
    HEALTH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": ts.isoformat(),
        "signal_file": latest.name,
        "signal_age_min": round(age_min, 1),
        "warn": warn_syms,
        "parked": park_syms,
        "symbols": {
            s.get("symbol"): {
                "guard": s.get("live_guard"),
                "pos": s.get("target_position"),
                "sr_6m": s.get("live_6m_sharpe"),
                "sr_30d": s.get("rolling_30d_sharpe"),
            } for s in signals
        },
    }
    out = HEALTH_LOG_DIR / f"health_{ts.strftime('%Y%m%d_%H%M')}.json"
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
