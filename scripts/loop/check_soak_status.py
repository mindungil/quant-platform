#!/usr/bin/env python3
"""Soak-pass verdict for the v4.5 5-day autonomous monitoring loop.

Reads data/loop/state.json + snapshots.jsonl and produces a structured
verdict: in_progress | pass | fail. Idempotent — safe to call every iter.
On terminal verdict (pass/fail) writes data/loop/soak_verdict.json and
sets state.json["soak_verdict"] so the loop can stop scheduling itself.

PASS criteria (ALL must hold):
  - target_end reached (now >= state.target_end)
  - no force-stop condition tripped during soak
  - paper drawdown stayed <= 25% throughout
  - daily-return |diff| did NOT exceed 200bps for 3+ consecutive days

FAIL criteria (ANY trips):
  - paper drawdown > 25% at any snapshot (force-stop)
  - daily-return |diff| > 200bps sustained 3+ consecutive days

In_progress otherwise.

Usage:
  python3 scripts/loop/check_soak_status.py            # human-readable
  python3 scripts/loop/check_soak_status.py --json     # JSON
  python3 scripts/loop/check_soak_status.py --write    # persist verdict to state.json on terminal status
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))
SNAPSHOTS_PATH = Path(os.getenv("LOOP_SNAPSHOTS_PATH", "/home/ubuntu/quant/data/loop/snapshots.jsonl"))
VERDICT_PATH = Path(os.getenv("LOOP_VERDICT_PATH", "/home/ubuntu/quant/data/loop/soak_verdict.json"))

DD_FORCE_STOP = 0.25       # paper drawdown above this → fail
DRIFT_BPS_LIMIT = 200.0    # |daily_ret_diff_bps| above this counts as drift day
DRIFT_DAYS_LIMIT = 3       # this many consecutive drift days → fail


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime:
    # tolerate trailing Z
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit(f"state file not found: {STATE_PATH}")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _load_snapshots() -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []
    out = []
    for line in SNAPSHOTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _drift_streak(snaps: list[dict]) -> int:
    """Longest tail run of consecutive distinct-UTC-date snapshots whose
    |daily_ret_diff_bps| exceeded DRIFT_BPS_LIMIT.

    We count UTC days (not iters) since drift is a daily phenomenon and
    snapshots are produced ~hourly. A 'drift day' = at least one snapshot
    on that UTC date with |diff| > limit.
    """
    if not snaps:
        return 0
    by_date: dict[str, bool] = {}
    for s in snaps:
        ts = s.get("ts")
        diff = s.get("daily_ret_diff_bps")
        if not ts or diff is None:
            continue
        try:
            d = _parse_iso(ts).date().isoformat()
        except ValueError:
            continue
        is_drift = abs(float(diff)) > DRIFT_BPS_LIMIT
        # Once a date is marked True, keep it True.
        by_date[d] = by_date.get(d, False) or is_drift

    if not by_date:
        return 0
    # Walk dates in chronological order and find max trailing run of True
    dates_sorted = sorted(by_date.keys())
    streak = 0
    best_tail = 0
    for d in dates_sorted:
        if by_date[d]:
            streak += 1
        else:
            streak = 0
        best_tail = streak  # tail run ends at the latest date
    return best_tail


def _max_dd_observed(snaps: list[dict]) -> float:
    return max((float(s.get("max_dd") or 0) for s in snaps), default=0.0)


def evaluate(state: dict, snaps: list[dict], now: datetime | None = None) -> dict:
    now = now or _utcnow()
    target_end = state.get("target_end")
    if not target_end:
        return {"status": "fail", "reasons": ["state.target_end missing"], "now": now.isoformat()}
    end_dt = _parse_iso(target_end)

    fail_reasons: list[str] = []
    info: dict = {
        "iter": state.get("iteration_count"),
        "snapshots": len(snaps),
        "now": now.isoformat(),
        "target_end": target_end,
        "elapsed_seconds": (now - _parse_iso(state.get("started_at", target_end))).total_seconds()
        if state.get("started_at") else None,
        "remaining_seconds": (end_dt - now).total_seconds(),
    }

    # Force-stop: max DD in any snapshot
    dd = _max_dd_observed(snaps)
    info["max_dd_observed"] = dd
    if dd > DD_FORCE_STOP:
        fail_reasons.append(f"paper drawdown {dd*100:.1f}% > {DD_FORCE_STOP*100:.0f}% force-stop")

    # Drift days: consecutive UTC dates where |daily_ret_diff_bps| > 200
    streak = _drift_streak(snaps)
    info["drift_day_streak"] = streak
    if streak >= DRIFT_DAYS_LIMIT:
        fail_reasons.append(
            f"daily-ret |diff|>200bps sustained {streak} days (limit {DRIFT_DAYS_LIMIT})"
        )

    # Terminal evaluation
    if fail_reasons:
        return {**info, "status": "fail", "reasons": fail_reasons}

    if now >= end_dt:
        return {
            **info,
            "status": "pass",
            "reasons": [
                f"target_end reached ({target_end})",
                f"max_dd {dd*100:.1f}% within {DD_FORCE_STOP*100:.0f}% bound",
                f"drift streak {streak} day(s) within {DRIFT_DAYS_LIMIT} bound",
            ],
        }

    return {
        **info,
        "status": "in_progress",
        "reasons": [
            f"~{info['remaining_seconds']/86400:.2f}d remaining until target_end",
            f"max_dd {dd*100:.1f}% (force-stop at {DD_FORCE_STOP*100:.0f}%)",
            f"drift streak {streak}d (force-stop at {DRIFT_DAYS_LIMIT}d)",
        ],
    }


def _persist(state: dict, verdict: dict) -> None:
    """Write verdict to state.json + standalone soak_verdict.json. Only
    writes state.json on terminal status (pass/fail) — in_progress would
    just churn the file every iter.
    """
    if verdict["status"] in ("pass", "fail"):
        VERDICT_PATH.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
        state["soak_verdict"] = verdict
        STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Soak-pass verdict for v4.5 monitoring loop")
    parser.add_argument("--json", action="store_true", help="Emit JSON verdict")
    parser.add_argument("--write", action="store_true",
                        help="Persist verdict to state.json + soak_verdict.json on terminal status")
    parser.add_argument("--exit-code", action="store_true",
                        help="Exit 0=in_progress, 2=pass, 3=fail (loop runners use this)")
    parser.add_argument("--push-telegram", action="store_true",
                        help="Push the verdict to Telegram on terminal status (idempotent)")
    args = parser.parse_args()

    state = _load_state()
    snaps = _load_snapshots()
    verdict = evaluate(state, snaps)

    if args.write:
        _persist(state, verdict)

    if args.push_telegram and verdict["status"] in ("pass", "fail"):
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from scripts.loop.notify_telegram import push_from_verdict  # type: ignore
            from shared.notifications.telegram import TelegramNotifier  # type: ignore
            push_from_verdict(TelegramNotifier(), dry_run=False)
        except Exception as exc:
            print(f"telegram push skipped ({type(exc).__name__}: {exc})", file=sys.stderr)

    if args.json:
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
    else:
        print(f"[{verdict['status'].upper()}] iter={verdict.get('iter')} snapshots={verdict.get('snapshots')}")
        for r in verdict.get("reasons", []):
            print(f"  - {r}")
        if verdict["status"] == "pass":
            print(f"  → PASS verdict written to {VERDICT_PATH}")
        elif verdict["status"] == "fail":
            print(f"  → FAIL verdict written to {VERDICT_PATH}")

    if args.exit_code:
        return {"in_progress": 0, "pass": 2, "fail": 3}.get(verdict["status"], 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
