#!/usr/bin/env python3
"""Telegram bridge for the v4.5 soak loop — pushes new anomaly narrations
and terminal soak verdicts to the user's Telegram chat.

Idempotent by design:
  * Narrations carry a `telegram_sent_at` field once pushed; subsequent
    runs skip them. Only severity ∈ {warn, critical} is pushed.
  * Soak verdict is pushed at most once (state.soak_verdict_telegram_sent_at).

Usage:
  # Push the newest unsent warn/critical narration (no-op if none new)
  python3 scripts/loop/notify_telegram.py --from-narration

  # Push soak verdict if terminal (pass/fail) and not yet sent
  python3 scripts/loop/notify_telegram.py --from-verdict

  # Push a one-off message
  python3 scripts/loop/notify_telegram.py --message "hello" --severity warn

  # Dry-run: render but don't send (works without TELEGRAM_BOT_TOKEN/CHAT_ID)
  python3 scripts/loop/notify_telegram.py --from-narration --dry-run

Environment:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — required for actual send.
  LOOP_STATE_PATH — defaults to data/loop/state.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from shared.notifications.telegram import TelegramNotifier, AlertLevel  # noqa: E402

STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))

_LEVEL_ICON = {
    "info":     AlertLevel.INFO,
    "warn":     AlertLevel.WARNING,
    "warning":  AlertLevel.WARNING,
    "critical": AlertLevel.CRITICAL,
    "fail":     AlertLevel.CRITICAL,
    "pass":     AlertLevel.PROFIT,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit(f"state file not found: {STATE_PATH}")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _format_narration(entry: dict, deployment_version: str | None) -> str:
    sev = (entry.get("severity") or "info").lower()
    icon = _LEVEL_ICON.get(sev, AlertLevel.INFO)
    lines = [
        f"{icon} <b>v4.5 soak — {sev.upper()}</b>",
        f"<i>iter {entry.get('iter')} · {entry.get('ts','?')}</i>",
        "",
        f"<b>obs:</b> {entry.get('observation','—')}",
    ]
    if entry.get("action_taken"):
        lines.append(f"<b>action:</b> {entry['action_taken']}")
    narration = entry.get("narration") or ""
    if narration and not narration.startswith("[skipped"):
        # truncate long narrations
        if len(narration) > 800:
            narration = narration[:800] + "…"
        lines += ["", narration]
    if deployment_version:
        lines += ["", f"<i>deployment: {deployment_version}</i>"]
    return "\n".join(lines)


def _format_verdict(verdict: dict, state: dict) -> str:
    status = (verdict.get("status") or "?").lower()
    icon = _LEVEL_ICON.get(status, AlertLevel.INFO)
    deployment_version = (state.get("deployment") or {}).get("version")
    lines = [
        f"{icon} <b>5-day soak verdict: {status.upper()}</b>",
        f"<i>iter {verdict.get('iter')} · {verdict.get('now','?')}</i>",
        "",
    ]
    if verdict.get("max_dd_observed") is not None:
        lines.append(f"max_dd: {verdict['max_dd_observed']*100:.1f}%")
    if verdict.get("drift_day_streak") is not None:
        lines.append(f"drift streak: {verdict['drift_day_streak']}d")
    lines.append("")
    for r in (verdict.get("reasons") or [])[:6]:
        lines.append(f"• {r}")
    if deployment_version:
        lines += ["", f"<i>deployment: {deployment_version}</i>"]
    return "\n".join(lines)


def _push(notifier: TelegramNotifier, message: str, dry_run: bool) -> bool:
    """Send (or pretend-send under --dry-run). Returns True if delivery
    counted as success (always True under dry-run; True iff Telegram API
    returned ok otherwise).
    """
    if dry_run:
        print("[DRY-RUN] would send:")
        print("---")
        print(message)
        print("---")
        return True
    if not notifier.enabled:
        print("[skip] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — not sending")
        return False
    ok = notifier.send(message)
    if ok:
        print("[sent] Telegram delivery ok")
    else:
        print("[fail] Telegram delivery failed (see logs)")
    return ok


def push_from_narration(notifier: TelegramNotifier, dry_run: bool) -> int:
    """Push the newest unsent warn/critical narration. No-op if none.
    Returns 0 on success/no-op, 1 on send failure.
    """
    state = _load_state()
    narrations = state.get("anomaly_narrations") or []
    deployment_version = (state.get("deployment") or {}).get("version")

    target = None
    target_idx = -1
    for i in range(len(narrations) - 1, -1, -1):
        n = narrations[i]
        sev = (n.get("severity") or "").lower()
        if sev not in ("warn", "warning", "critical"):
            continue
        if n.get("telegram_sent_at"):
            # already sent — and since we're walking newest→oldest, anything
            # older is also already sent or older than the last sent.
            break
        target, target_idx = n, i
        break

    if target is None:
        print("[skip] no new warn/critical narration to push")
        return 0

    msg = _format_narration(target, deployment_version)
    ok = _push(notifier, msg, dry_run)
    if ok and not dry_run:
        narrations[target_idx]["telegram_sent_at"] = _now_iso()
        state["anomaly_narrations"] = narrations
        _write_state(state)
    return 0 if ok else 1


def push_from_verdict(notifier: TelegramNotifier, dry_run: bool) -> int:
    """Push soak verdict if terminal (pass/fail) and not yet sent."""
    state = _load_state()
    verdict = state.get("soak_verdict")
    if not verdict:
        print("[skip] no soak_verdict in state.json (run check_soak_status.py --write first)")
        return 0
    status = (verdict.get("status") or "").lower()
    if status not in ("pass", "fail"):
        print(f"[skip] verdict status={status!r} — only pass/fail are pushed")
        return 0
    if state.get("soak_verdict_telegram_sent_at"):
        print("[skip] verdict already sent")
        return 0

    msg = _format_verdict(verdict, state)
    ok = _push(notifier, msg, dry_run)
    if ok and not dry_run:
        state["soak_verdict_telegram_sent_at"] = _now_iso()
        _write_state(state)
    return 0 if ok else 1


def push_message(notifier: TelegramNotifier, message: str, severity: str, dry_run: bool) -> int:
    icon = _LEVEL_ICON.get(severity.lower(), AlertLevel.INFO)
    msg = f"{icon} <b>v4.5 soak</b>\n{message}"
    ok = _push(notifier, msg, dry_run)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram bridge for v4.5 soak loop")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-narration", action="store_true",
                     help="Push newest unsent warn/critical narration")
    src.add_argument("--from-verdict", action="store_true",
                     help="Push soak verdict if terminal and unsent")
    src.add_argument("--message", help="Push an explicit message (use with --severity)")
    parser.add_argument("--severity", default="info",
                        choices=["info", "warn", "warning", "critical", "fail", "pass"])
    parser.add_argument("--dry-run", action="store_true",
                        help="Render and print only, do not send (no env required)")
    args = parser.parse_args()

    notifier = TelegramNotifier()

    if args.from_narration:
        return push_from_narration(notifier, args.dry_run)
    if args.from_verdict:
        return push_from_verdict(notifier, args.dry_run)
    if args.message:
        return push_message(notifier, args.message, args.severity, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
