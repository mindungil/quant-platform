#!/usr/bin/env python3
"""Post-soak action advisor.

Reads state.soak_verdict and emits the recommended operator next-action
sequence for each terminal status. Optionally executes the safe steps
(`--auto`) — never flips to live without explicit confirmation.

PASS path:
  1. preflight_check.py --require-live   (gates verified)
  2. ramp_controller.py init             (stage clock starts)
  3. enable alpha_health_daily cron      (once-a-day monitoring)
  4. operator: edit config/execution_mode.json mode→live + export LIVE_TRADING_ENABLED=true

FAIL path:
  1. show fail reasons
  2. ramp_controller.py rollback (if previously promoted)
  3. preserve halt.flag (don't auto-clear)
  4. defer transition; investigate root cause

IN_PROGRESS:
  1. show estimated remaining time
  2. show current stop-condition slack

Usage:
  python3 scripts/loop/post_soak_actions.py
  python3 scripts/loop/post_soak_actions.py --json
  python3 scripts/loop/post_soak_actions.py --auto       # runs safe steps (preflight only)
  python3 scripts/loop/post_soak_actions.py --push-telegram
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

LOOP_STATE_PATH = Path(os.getenv("LOOP_STATE_PATH",
                                 str(REPO_ROOT / "data" / "loop" / "state.json")))


def _load_state() -> dict:
    if not LOOP_STATE_PATH.exists():
        sys.exit(f"state file not found: {LOOP_STATE_PATH}")
    return json.loads(LOOP_STATE_PATH.read_text(encoding="utf-8"))


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _build_advice(state: dict) -> dict:
    verdict = state.get("soak_verdict") or {}
    status = (verdict.get("status") or "in_progress").lower()
    advice = {
        "status": status,
        "verdict": verdict,
        "next_actions": [],
        "warnings": [],
    }

    if status == "pass":
        advice["headline"] = "Soak PASSED — proceed to live ramp"
        advice["next_actions"] = [
            {"step": 1, "cmd": "python3 scripts/live/preflight_check.py --exchange binance --require-live",
             "auto": True,
             "purpose": "Verify all live-mode prereqs (env, kill switch, credentials, ramp, alpha health)"},
            {"step": 2, "cmd": "python3 scripts/live/ramp_controller.py init",
             "auto": False,
             "purpose": "Start stage clock for first ramp factor (manual: confirms operator awareness)"},
            {"step": 3, "cmd": "python3 scripts/live/alpha_health_daily.py --push-telegram",
             "auto": True,
             "purpose": "Snapshot today's alpha health into state for tracking"},
            {"step": 4, "cmd": "edit config/execution_mode.json: mode='live'; export LIVE_TRADING_ENABLED=true",
             "auto": False,
             "purpose": "Manual flip — irreversible commitment, never auto"},
            {"step": 5, "cmd": "python3 scripts/live/risk_daemon_binance.py --api-key ... --api-secret ...",
             "auto": False,
             "purpose": "Start live risk daemon (in separate terminal/systemd unit)"},
        ]
        # Reality checks
        ah = state.get("alpha_health") or {}
        crit = [s for s, e in ah.items() if int(e.get("consecutive_fail_days", 0)) >= 14]
        warn = [s for s, e in ah.items() if 7 <= int(e.get("consecutive_fail_days", 0)) < 14]
        if crit:
            advice["warnings"].append(f"Alpha critical streak still active for: {','.join(crit)} — consider pausing")
        if warn:
            advice["warnings"].append(f"Alpha warn streak for: {','.join(warn)} — monitor closely")

    elif status == "fail":
        advice["headline"] = "Soak FAILED — DO NOT promote to live"
        advice["next_actions"] = [
            {"step": 1, "cmd": "review verdict reasons (printed above)", "auto": False,
             "purpose": "Identify root cause — DD breach? drift? alpha collapse?"},
            {"step": 2, "cmd": "python3 scripts/live/ramp_controller.py rollback", "auto": False,
             "purpose": "If any ramp was started, step back one stage"},
            {"step": 3, "cmd": "halt.flag preserved — manual clear ONLY after triage",
             "auto": False,
             "purpose": "Risk daemon halt remains active until operator clears /home/ubuntu/quant/data/state/halt.flag"},
            {"step": 4, "cmd": "python3 scripts/live/pnl_attribution.py --since <baseline>",
             "auto": True,
             "purpose": "See which alpha contributed to the failure"},
            {"step": 5, "cmd": "address root cause, restart soak from new baseline",
             "auto": False, "purpose": "Do not re-attempt without changes"},
        ]
        advice["warnings"].append("DO NOT flip mode to live until root cause is identified and addressed.")

    else:  # in_progress
        target_end = state.get("target_end")
        now = datetime.now(timezone.utc)
        remaining = None
        if target_end:
            try:
                remaining = (_parse_iso(target_end) - now).total_seconds() / 86400
            except ValueError:
                pass
        advice["headline"] = (
            f"Soak in progress — {remaining:.2f} days remaining" if remaining is not None
            else "Soak in progress"
        )
        last_snap = state.get("last_snapshot") or {}
        max_dd = last_snap.get("max_dd")
        drift_bps = last_snap.get("daily_ret_diff_bps")
        slack = []
        if max_dd is not None:
            slack.append(f"DD slack: {(0.25 - float(max_dd))*100:.1f}pp until force-stop")
        if drift_bps is not None:
            slack.append(f"drift slack: {200 - abs(float(drift_bps)):.0f}bps until force-stop")
        advice["next_actions"] = [
            {"step": 1, "cmd": "wait — auto-loop continues",
             "auto": False,
             "purpose": " · ".join(slack) if slack else "loop will produce verdict at target_end"},
            {"step": 2, "cmd": "python3 scripts/loop/check_soak_status.py --write --push-telegram",
             "auto": True,
             "purpose": "Re-evaluate now (idempotent)"},
        ]

    return advice


def render_text(advice: dict) -> str:
    lines = [f"# {advice['headline']}"]
    if advice.get("verdict"):
        v = advice["verdict"]
        lines.append(f"  verdict status: {v.get('status', '?').upper()} (iter {v.get('iter', '?')})")
        for r in (v.get("reasons") or [])[:5]:
            lines.append(f"    - {r}")
    lines += ["", "## Next actions"]
    for a in advice["next_actions"]:
        marker = " (auto-runnable)" if a.get("auto") else " (manual)"
        lines.append(f"  {a['step']}. {a['cmd']}")
        lines.append(f"     → {a['purpose']}{marker}")
    if advice.get("warnings"):
        lines += ["", "## Warnings"]
        for w in advice["warnings"]:
            lines.append(f"  ⚠ {w}")
    return "\n".join(lines)


def execute_safe_steps(advice: dict) -> list[dict]:
    """Run only the steps marked `auto=True`. Returns per-step results."""
    results: list[dict] = []
    for a in advice["next_actions"]:
        if not a.get("auto"):
            continue
        cmd = a["cmd"]
        if not cmd.startswith("python3 "):  # never exec edit/operator-only steps
            results.append({"step": a["step"], "cmd": cmd, "skipped": "non-python step"})
            continue
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                  cwd=str(REPO_ROOT), timeout=120)
            results.append({
                "step": a["step"], "cmd": cmd,
                "exit_code": proc.returncode,
                "stdout_tail": proc.stdout.splitlines()[-5:],
                "stderr_tail": proc.stderr.splitlines()[-3:],
            })
        except Exception as e:
            results.append({"step": a["step"], "cmd": cmd, "error": str(e)})
    return results


def push_to_telegram(advice: dict) -> None:
    try:
        from shared.notifications.telegram import TelegramNotifier, AlertLevel
        notifier = TelegramNotifier()
        if not notifier.enabled:
            print("  → telegram: not configured, skip")
            return
        status = advice["status"]
        icon = {"pass": AlertLevel.PROFIT, "fail": AlertLevel.CRITICAL,
                "in_progress": AlertLevel.INFO}.get(status, AlertLevel.INFO)
        lines = [f"{icon} <b>{advice['headline']}</b>"]
        for a in advice["next_actions"][:4]:
            lines.append(f"<code>{a['step']}.</code> {a['cmd'][:100]}")
        if advice.get("warnings"):
            lines.append("")
            for w in advice["warnings"][:3]:
                lines.append(f"⚠ {w}")
        notifier.send("\n".join(lines))
        print("  → telegram: sent")
    except Exception as e:
        print(f"  → telegram skipped ({type(e).__name__}: {e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-soak action advisor")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--auto", action="store_true",
                        help="Execute auto-runnable safe steps (never the manual ones)")
    parser.add_argument("--push-telegram", action="store_true")
    args = parser.parse_args()

    state = _load_state()
    advice = _build_advice(state)

    if args.json:
        out = dict(advice)
        if args.auto:
            out["execution_results"] = execute_safe_steps(advice)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(render_text(advice))
        if args.auto:
            print("\n## Auto-execution results")
            for r in execute_safe_steps(advice):
                print(f"  step {r.get('step')}: exit={r.get('exit_code', r.get('error', 'skipped'))}")

    if args.push_telegram:
        push_to_telegram(advice)
    return 0


if __name__ == "__main__":
    sys.exit(main())
