#!/usr/bin/env python3
"""Daily / weekly PnL budget guard.

Tracks paper (or live) equity day-over-day and trips a halt.flag when
losses exceed configured budget. Stays *off* by default until explicitly
enabled — same posture as the rest of the safety stack: opt-in, never
surprise.

Budgets (override via CLI or env):
  --daily-budget-pct   default -1.0 (-1%)   day's PnL <= this → halt
  --weekly-budget-pct  default -3.0 (-3%)   trailing-7d PnL <= this → halt

Interaction with halt.flag:
  Reads existing halt.flag — if already halted, only reports without
  modifying. Setting an additional halt would mask the original cause.

Source for equity:
  --source paper (default)  → data/paper/portfolio_history.jsonl
  --source virtual          → data/virtual/history.jsonl (parses fills)
  --source loop             → data/loop/state.json + snapshots

Usage:
  python3 scripts/live/pnl_budget_guard.py
  python3 scripts/live/pnl_budget_guard.py --daily-budget-pct -0.5 --weekly-budget-pct -2.0
  python3 scripts/live/pnl_budget_guard.py --enforce       # actually set halt.flag on breach
  python3 scripts/live/pnl_budget_guard.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta, date as date_cls
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

PAPER_HISTORY = REPO_ROOT / "data" / "paper" / "portfolio_history.jsonl"
VIRTUAL_HISTORY = REPO_ROOT / "data" / "virtual" / "history.jsonl"
LOOP_STATE = REPO_ROOT / "data" / "loop" / "state.json"
SNAPSHOTS = REPO_ROOT / "data" / "loop" / "snapshots.jsonl"
HALT_FLAG = Path(os.getenv("HALT_FLAG_PATH", "/home/ubuntu/quant/data/state/halt.flag"))


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _equity_series(source: str) -> list[tuple[datetime, float]]:
    """Return list of (ts, equity) sorted by ts."""
    if source == "paper":
        out = []
        for r in _read_jsonl(PAPER_HISTORY):
            try:
                out.append((_parse_iso(r["timestamp"]), float(r["capital"])))
            except (KeyError, ValueError):
                continue
        return sorted(out, key=lambda x: x[0])
    if source == "virtual":
        out = []
        for r in _read_jsonl(VIRTUAL_HISTORY):
            if "equity_after" not in r:
                continue
            try:
                out.append((_parse_iso(r["timestamp"]), float(r["equity_after"])))
            except (KeyError, ValueError):
                continue
        return sorted(out, key=lambda x: x[0])
    if source == "loop":
        out = []
        for r in _read_jsonl(SNAPSHOTS):
            try:
                out.append((_parse_iso(r["ts"]), float(r["paper"])))
            except (KeyError, ValueError):
                continue
        return sorted(out, key=lambda x: x[0])
    raise ValueError(f"unknown source: {source}")


def _equity_at_or_before(series: list[tuple[datetime, float]], cutoff: datetime) -> float | None:
    """Equity value at or just before cutoff. None if no record."""
    candidates = [eq for ts, eq in series if ts <= cutoff]
    return candidates[-1] if candidates else None


def evaluate(source: str, daily_budget_pct: float, weekly_budget_pct: float) -> dict:
    series = _equity_series(source)
    if not series:
        return {"source": source, "status": "no_data"}

    now = datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    week_ago = now - timedelta(days=7)

    eq_now = series[-1][1]
    eq_today_open = _equity_at_or_before(series, today_start)
    eq_week_ago = _equity_at_or_before(series, week_ago)

    daily_pnl_pct = ((eq_now - eq_today_open) / eq_today_open * 100) if eq_today_open else None
    weekly_pnl_pct = ((eq_now - eq_week_ago) / eq_week_ago * 100) if eq_week_ago else None

    halt_already = HALT_FLAG.exists()
    halt_info = {}
    if halt_already:
        try:
            halt_info = json.loads(HALT_FLAG.read_text())
        except Exception:
            pass

    breaches = []
    if daily_pnl_pct is not None and daily_pnl_pct <= daily_budget_pct:
        breaches.append({
            "type": "daily",
            "pnl_pct": daily_pnl_pct,
            "budget_pct": daily_budget_pct,
            "ref_equity": eq_today_open,
        })
    if weekly_pnl_pct is not None and weekly_pnl_pct <= weekly_budget_pct:
        breaches.append({
            "type": "weekly",
            "pnl_pct": weekly_pnl_pct,
            "budget_pct": weekly_budget_pct,
            "ref_equity": eq_week_ago,
        })

    status = "pass"
    if breaches:
        status = "halt_recommended"
    if halt_already:
        status = "already_halted"

    return {
        "source": source,
        "status": status,
        "ts_now": now.isoformat(),
        "equity_now": eq_now,
        "equity_today_open": eq_today_open,
        "equity_week_ago": eq_week_ago,
        "daily_pnl_pct": daily_pnl_pct,
        "weekly_pnl_pct": weekly_pnl_pct,
        "daily_budget_pct": daily_budget_pct,
        "weekly_budget_pct": weekly_budget_pct,
        "breaches": breaches,
        "halt_flag_exists": halt_already,
        "halt_flag_info": halt_info,
    }


def render_text(report: dict) -> str:
    if report["status"] == "no_data":
        return f"# PnL budget guard — no data ({report['source']})"
    lines = [
        f"# PnL budget guard — source={report['source']}  status={report['status'].upper()}",
        f"  equity now: ${report['equity_now']:,.2f}",
    ]
    if report.get("daily_pnl_pct") is not None:
        lines.append(f"  daily PnL: {report['daily_pnl_pct']:+.3f}%  (budget {report['daily_budget_pct']:+.2f}%)")
    if report.get("weekly_pnl_pct") is not None:
        lines.append(f"  weekly PnL: {report['weekly_pnl_pct']:+.3f}%  (budget {report['weekly_budget_pct']:+.2f}%)")
    if report["breaches"]:
        lines += ["", "## Breaches"]
        for b in report["breaches"]:
            lines.append(f"  ⚠ {b['type']}: {b['pnl_pct']:+.3f}% <= budget {b['budget_pct']:+.2f}%")
    if report["halt_flag_exists"]:
        lines += ["", f"## Existing halt.flag", f"  {report['halt_flag_info']}"]
    return "\n".join(lines)


def enforce_halt(report: dict) -> bool:
    if report["status"] != "halt_recommended":
        return False
    HALT_FLAG.parent.mkdir(parents=True, exist_ok=True)
    HALT_FLAG.write_text(json.dumps({
        "halted_at": datetime.now(timezone.utc).isoformat(),
        "reason": f"pnl_budget_guard breach: {[b['type'] for b in report['breaches']]}",
        "details": report["breaches"],
    }, indent=2))
    return True


def push_to_telegram(report: dict) -> None:
    if report["status"] not in ("halt_recommended", "already_halted"):
        return
    try:
        from shared.notifications.telegram import TelegramNotifier, AlertLevel
        notifier = TelegramNotifier()
        if not notifier.enabled:
            return
        lines = [f"{AlertLevel.CRITICAL} <b>PnL budget breach</b>",
                 f"<i>source={report['source']}</i>", ""]
        if report.get("daily_pnl_pct") is not None:
            lines.append(f"daily: {report['daily_pnl_pct']:+.3f}% / budget {report['daily_budget_pct']:+.2f}%")
        if report.get("weekly_pnl_pct") is not None:
            lines.append(f"weekly: {report['weekly_pnl_pct']:+.3f}% / budget {report['weekly_budget_pct']:+.2f}%")
        for b in report["breaches"]:
            lines.append(f"• {b['type']} breach")
        notifier.send("\n".join(lines))
    except Exception as e:
        print(f"  → telegram skipped ({type(e).__name__}: {e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily/weekly PnL budget guard")
    parser.add_argument("--source", choices=["paper", "virtual", "loop"], default="paper")
    parser.add_argument("--daily-budget-pct", type=float, default=-1.0,
                        help="Halt when day PnL <= this percent (default -1.0)")
    parser.add_argument("--weekly-budget-pct", type=float, default=-3.0,
                        help="Halt when 7d PnL <= this percent (default -3.0)")
    parser.add_argument("--enforce", action="store_true",
                        help="Actually set halt.flag on breach (default: report only)")
    parser.add_argument("--push-telegram", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = evaluate(args.source, args.daily_budget_pct, args.weekly_budget_pct)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(report))

    if args.enforce and report["status"] == "halt_recommended":
        if enforce_halt(report):
            print(f"\n  → halt.flag SET at {HALT_FLAG}")

    if args.push_telegram:
        push_to_telegram(report)

    return {"pass": 0, "halt_recommended": 2,
            "already_halted": 0, "no_data": 0}.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
