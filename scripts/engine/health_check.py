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
VIRTUAL_STATE = REPO_ROOT / "data" / "virtual" / "state.json"
HEALTH_LOG_DIR = REPO_ROOT / "data" / "metrics" / "health_log"
LOOP_STATE = REPO_ROOT / "data" / "loop" / "state.json"
LOOP_SNAPSHOTS = REPO_ROOT / "data" / "loop" / "snapshots.jsonl"
UTC = timezone.utc

# Symbol → snapshot SR field. Consumed by oos_tracker_30d._SR_FIELDS, daily_digest,
# narrate_anomaly. Add a new line here when extending the universe (and update the
# reader's _SR_FIELDS to match) so the live OOS bands cover the new symbol.
_SNAPSHOT_SR_FIELDS = {
    "BTCUSDT": "btc_6m_sr",
    "ETHUSDT": "eth_6m_sr",
    "BNBUSDT": "bnb_6m_sr",
    "SOLUSDT": "sol_6m_sr",
}


def _latest_signal_file() -> Path | None:
    SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(SIGNALS_DIR.glob("signals_*.json"), reverse=True)
    return files[0] if files else None


def _safe_load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_loop_snapshot(ts: datetime, signals: list[dict], warn_syms: list[str]) -> None:
    """Append a snapshot record to data/loop/snapshots.jsonl and refresh state.json.

    Resurrects the soak-loop snapshot pipeline so oos_tracker_30d, alpha_health_daily,
    and narrate_anomaly keep getting fresh per-symbol SR + paper/virtual equity even
    when a dedicated loop driver isn't running. SOL is included via _SNAPSHOT_SR_FIELDS.
    """
    sym_sr = {sig.get("symbol"): sig.get("live_6m_sharpe") for sig in signals if "symbol" in sig}
    sym_sr_30d = {sig.get("symbol"): sig.get("rolling_30d_sharpe") for sig in signals if "symbol" in sig}

    paper = _safe_load_json(PAPER_STATE) or {}
    virtual = _safe_load_json(VIRTUAL_STATE) or {}
    loop_state = _safe_load_json(LOOP_STATE) or {}

    paper_eq = float(paper.get("capital", 0.0) or 0.0)
    virtual_eq = float(virtual.get("equity", 0.0) or 0.0)
    max_dd = float(paper.get("max_drawdown", 0.0) or 0.0)
    n_trades = int(paper.get("n_trades", 0) or 0)

    # daily_ret_diff_bps: paper vs virtual return delta over the previous 24h.
    # Approximated via deltas vs the last persisted snapshot (~1h apart). Set to
    # None when we don't yet have a baseline — readers already tolerate missing.
    last_snap = loop_state.get("last_snapshot") or {}
    last_paper = last_snap.get("paper")
    last_virtual = last_snap.get("virtual")
    if isinstance(last_paper, (int, float)) and isinstance(last_virtual, (int, float)) \
            and last_paper > 0 and last_virtual > 0:
        paper_ret = (paper_eq - last_paper) / last_paper
        virtual_ret = (virtual_eq - last_virtual) / last_virtual
        daily_ret_diff_bps = round((paper_ret - virtual_ret) * 1e4, 1)
    else:
        daily_ret_diff_bps = None

    snapshot: dict = {
        "iter": int(loop_state.get("iteration_count", 0) or 0) + 1,
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "paper": round(paper_eq, 2),
        "virtual": round(virtual_eq, 2),
        "daily_ret_diff_bps": daily_ret_diff_bps,
        "btc_30d_sr": sym_sr_30d.get("BTCUSDT"),
        "max_dd": round(max_dd, 4),
        "warn_symbols": warn_syms,
        "n_trades": n_trades,
        "anomalies": [],
    }
    for sym, field in _SNAPSHOT_SR_FIELDS.items():
        snapshot[field] = sym_sr.get(sym)

    LOOP_SNAPSHOTS.parent.mkdir(parents=True, exist_ok=True)
    with LOOP_SNAPSHOTS.open("a") as fh:
        fh.write(json.dumps(snapshot) + "\n")

    loop_state["iteration_count"] = snapshot["iter"]
    loop_state["last_iter_at"] = ts.isoformat()
    loop_state["last_snapshot"] = {k: v for k, v in snapshot.items() if k not in ("iter", "ts", "anomalies")}
    tmp = LOOP_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(loop_state, indent=2))
    tmp.replace(LOOP_STATE)


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

    try:
        _write_loop_snapshot(ts, signals, warn_syms)
    except Exception as exc:
        print(f"  ? loop snapshot write failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
