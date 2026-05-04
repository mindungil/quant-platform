#!/usr/bin/env python3
"""Stress event detector + intensive monitoring window trigger.

5-day v4.5 soak ran in flat/recovery market only — risk-off behavior of
half_kelly was never observed under stress. This watcher flags stress
conditions and logs an "intensive monitoring" record so we know when to
demand a manual review of v4.5 performance under volatile regimes.

Trigger conditions (any one fires):
  1) BTC daily |return| ≥ 5% (24h close-to-close)
  2) BTC 1h drawdown from rolling 24h peak ≥ 3%
  3) Paper portfolio current_dd jumps ≥ 2% in the last hour

When fired:
  - Append event to data/loop/stress_events.jsonl
  - Update state.json.stress_window  (start_ts, reason, status="open" or "closed")
  - Stress windows stay open for `--window-hours` after the most recent trigger
  - Within an open window, this script is intended to be invoked at higher
    cadence (e.g., 15-min cron) — but the script itself is idempotent within
    the same hour.

Usage:
  python3 scripts/live/stress_monitor.py
  python3 scripts/live/stress_monitor.py --window-hours 8 --json
  python3 scripts/live/stress_monitor.py --threshold-daily 0.05 --threshold-1h-dd 0.03
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.backtest.alpha_validator import load_ohlcv_stitched  # noqa: E402

STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))
EVENT_LOG = Path(os.getenv("STRESS_EVENT_LOG", "/home/ubuntu/quant/data/loop/stress_events.jsonl"))
PAPER_STATE = Path("/home/ubuntu/quant/data/paper/portfolio_state.json")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text())


def save_state_atomic(state: dict):
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def append_event(record: dict):
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def check_btc_daily_return(threshold: float) -> tuple[bool, dict]:
    """Trigger 1: |24h return| ≥ threshold."""
    try:
        df = load_ohlcv_stitched("BTCUSDT")
    except FileNotFoundError:
        return False, {"trigger": "btc_daily", "reason": "no_data"}
    if len(df) < 25:
        return False, {"trigger": "btc_daily", "reason": "insufficient_history"}
    last = float(df["close"].iloc[-1])
    prev_24h = float(df["close"].iloc[-25])
    ret = (last - prev_24h) / prev_24h
    fired = abs(ret) >= threshold
    return fired, {
        "trigger": "btc_daily_return",
        "ret": round(ret, 4),
        "threshold": threshold,
        "last_close": last,
        "prev_24h_close": prev_24h,
    }


def check_btc_1h_drawdown_from_peak(threshold: float) -> tuple[bool, dict]:
    """Trigger 2: 1h DD from rolling 24h peak ≥ threshold."""
    try:
        df = load_ohlcv_stitched("BTCUSDT")
    except FileNotFoundError:
        return False, {"trigger": "btc_1h_dd", "reason": "no_data"}
    if len(df) < 25:
        return False, {"trigger": "btc_1h_dd", "reason": "insufficient_history"}
    last = float(df["close"].iloc[-1])
    peak_24h = float(df["high"].iloc[-25:].max())
    dd = (last - peak_24h) / peak_24h
    fired = dd <= -threshold  # negative DD means below peak
    return fired, {
        "trigger": "btc_1h_drawdown",
        "dd_from_24h_peak": round(dd, 4),
        "threshold_neg": -threshold,
        "last_close": last,
        "peak_24h": peak_24h,
    }


def check_paper_dd_jump(threshold: float) -> tuple[bool, dict]:
    """Trigger 3: paper portfolio cur_dd increased by ≥ threshold from last check."""
    if not PAPER_STATE.exists():
        return False, {"trigger": "paper_dd_jump", "reason": "no_paper_state"}
    paper = json.loads(PAPER_STATE.read_text())
    cur_dd = paper.get("max_drawdown", 0.0)  # this is current (running) DD in our state file
    state = load_state()
    last_seen = state.get("stress_window", {}).get("last_paper_dd", cur_dd)
    delta = cur_dd - last_seen
    fired = delta >= threshold
    return fired, {
        "trigger": "paper_dd_jump",
        "cur_dd": round(cur_dd, 4),
        "last_seen": round(last_seen, 4),
        "delta": round(delta, 4),
        "threshold": threshold,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold-daily", type=float, default=0.05,
                    help="BTC |24h return| threshold (default 5%%)")
    ap.add_argument("--threshold-1h-dd", type=float, default=0.03,
                    help="BTC 1h drawdown-from-24h-peak threshold (default 3%%)")
    ap.add_argument("--threshold-paper-jump", type=float, default=0.02,
                    help="Paper DD jump threshold within window (default 2%%)")
    ap.add_argument("--window-hours", type=int, default=8,
                    help="How long an open stress window stays open after last trigger")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    fired_checks = []
    fired_btc_d, det_d = check_btc_daily_return(args.threshold_daily)
    fired_btc_h, det_h = check_btc_1h_drawdown_from_peak(args.threshold_1h_dd)
    fired_paper, det_p = check_paper_dd_jump(args.threshold_paper_jump)
    if fired_btc_d:
        fired_checks.append(det_d)
    if fired_btc_h:
        fired_checks.append(det_h)
    if fired_paper:
        fired_checks.append(det_p)

    state = load_state()
    sw = state.get("stress_window", {}) or {}

    sw_status = sw.get("status", "closed")
    sw_opened_at = sw.get("opened_at")
    sw_last_trigger_ts = sw.get("last_trigger_ts")

    if fired_checks:
        if sw_status != "open":
            sw_status = "open"
            sw_opened_at = now.isoformat()
        sw_last_trigger_ts = now.isoformat()
        record = {
            "ts": now.isoformat(),
            "triggers": fired_checks,
            "window_status": "open",
        }
        append_event(record)
    elif sw_status == "open" and sw_last_trigger_ts:
        # Auto-close if window expired
        last_t = datetime.fromisoformat(sw_last_trigger_ts.replace("Z", "+00:00"))
        if now - last_t >= timedelta(hours=args.window_hours):
            sw_status = "closed"
            append_event({
                "ts": now.isoformat(),
                "triggers": [],
                "window_status": "closed",
                "closed_after_hours": args.window_hours,
            })

    # Snapshot last paper DD seen so the jump-trigger has a reference next call
    try:
        paper_dd = json.loads(PAPER_STATE.read_text()).get("max_drawdown", 0.0) if PAPER_STATE.exists() else 0.0
    except Exception:
        paper_dd = 0.0

    state["stress_window"] = {
        "status": sw_status,
        "opened_at": sw_opened_at,
        "last_trigger_ts": sw_last_trigger_ts,
        "window_hours": args.window_hours,
        "last_paper_dd": paper_dd,
        "last_check_ts": now.isoformat(),
    }
    save_state_atomic(state)

    out = {
        "ts": now.isoformat(),
        "fired": bool(fired_checks),
        "fired_triggers": fired_checks,
        "btc_daily": det_d,
        "btc_1h_dd": det_h,
        "paper_dd_jump": det_p,
        "window": state["stress_window"],
    }

    if args.json:
        print(json.dumps(out, indent=2))
        return 1 if fired_checks else 0

    print("=" * 78)
    print(f"  STRESS MONITOR — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 78)
    print(f"  BTC 24h ret:     {det_d.get('ret', '?')}  (threshold ±{args.threshold_daily})")
    print(f"  BTC 1h DD/24h pk: {det_h.get('dd_from_24h_peak', '?')}  (threshold -{args.threshold_1h_dd})")
    print(f"  Paper DD jump:    {det_p.get('delta', '?')}  (threshold +{args.threshold_paper_jump})")
    print()
    if fired_checks:
        print(f"  ⚠ STRESS WINDOW {sw_status.upper()} — {len(fired_checks)} trigger(s):")
        for t in fired_checks:
            print(f"    - {t['trigger']}")
        print(f"  opened_at={sw_opened_at}  closes after {args.window_hours}h of calm")
    else:
        print(f"  Window status: {sw_status}")
        if sw_status == "open":
            print(f"    last trigger: {sw_last_trigger_ts}")
            print(f"    will close at: {sw_last_trigger_ts} + {args.window_hours}h")
    return 1 if fired_checks else 0


if __name__ == "__main__":
    sys.exit(main())
