#!/usr/bin/env python3
"""Pre-flight check — single command go-live readiness verification.

Runs ~10 independent checks against current state and config:

  1. Execution mode resolution (mode.py)
  2. Live safety gates (LIVE_TRADING_ENABLED, kill switch level)
  3. Credentials present for the named exchange
  4. Reconcile log freshness + skipped-rate
  5. Drift log freshness + severity
  6. halt.flag (risk daemon panic state) absent
  7. Ramp factor + ramp_state.json consistency
  8. Soak verdict (pass required for --require-live)
  9. Alpha health: no consecutive_fail_days >= 14
 10. Telegram bridge configured

Each check produces {name, status: pass|warn|fail, detail, data}.

Usage:
  python3 scripts/live/preflight_check.py --exchange binance
  python3 scripts/live/preflight_check.py --exchange binance --require-live
  python3 scripts/live/preflight_check.py --json
  python3 scripts/live/preflight_check.py --push-telegram     # send if FAIL or --require-live

Exit codes:
  0 = PASS, 1 = WARN (operator-decision), 2 = FAIL (do not proceed)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

LOOP_STATE_PATH = Path(os.getenv("LOOP_STATE_PATH",
                                 str(REPO_ROOT / "data" / "loop" / "state.json")))
RAMP_STATE_PATH = Path(os.getenv("RAMP_STATE_PATH",
                                 str(REPO_ROOT / "data" / "loop" / "ramp_state.json")))
HALT_FLAG = Path(os.getenv("HALT_FLAG_PATH", "/home/ubuntu/quant/data/state/halt.flag"))
RECONCILE_DIR = Path(os.getenv("RECONCILE_DIR", "/home/ubuntu/quant/data/logs/reconciliation"))
DRIFT_DIR = Path(os.getenv("DRIFT_DIR", "/home/ubuntu/quant/data/logs/drift"))

# Freshness window — log files older than this are flagged
RECONCILE_MAX_AGE_HOURS = 6
DRIFT_MAX_AGE_HOURS = 24


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _last_jsonl_entry(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        last_line = ""
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last_line = line
        return json.loads(last_line) if last_line else None
    except (OSError, json.JSONDecodeError):
        return None


def _newest_in_dir(dirpath: Path) -> Path | None:
    if not dirpath.exists():
        return None
    files = sorted(dirpath.glob("*.jsonl"))
    return files[-1] if files else None


# ---------------- individual checks ----------------

def check_execution_mode() -> dict:
    try:
        from shared.execution.mode import get_execution_mode, describe
        ctx = get_execution_mode()
        return {
            "name": "execution_mode",
            "status": "pass",
            "detail": describe(),
            "data": {"mode": ctx.mode.value, "source": ctx.source},
        }
    except Exception as e:
        return {"name": "execution_mode", "status": "fail",
                "detail": f"cannot resolve execution mode ({e})",
                "data": {}}


def check_live_safety(require_live: bool) -> dict:
    """Live mode requires env=true + kill switch not PANIC."""
    try:
        from shared.execution.mode import get_execution_mode
        from shared.risk.kill_switch import is_kill_switch_active
        ctx = get_execution_mode()
        env_set = os.getenv("LIVE_TRADING_ENABLED", "").lower() == "true"
        active, level = is_kill_switch_active()
        problems = []
        if ctx.mode.value == "live":
            if not env_set:
                problems.append("LIVE_TRADING_ENABLED env != 'true'")
            if active and level == "PANIC":
                problems.append(f"kill switch in {level} (blocks live)")
        elif require_live:
            problems.append(f"mode is {ctx.mode.value!r}, expected 'live' under --require-live")
            if not env_set:
                problems.append("LIVE_TRADING_ENABLED env != 'true'")
        if problems:
            return {"name": "live_safety", "status": "fail",
                    "detail": "; ".join(problems),
                    "data": {"mode": ctx.mode.value, "env_set": env_set,
                             "kill_active": active, "kill_level": level}}
        return {"name": "live_safety", "status": "pass",
                "detail": f"mode={ctx.mode.value} kill={level} env={env_set}",
                "data": {"mode": ctx.mode.value, "env_set": env_set,
                         "kill_active": active, "kill_level": level}}
    except Exception as e:
        return {"name": "live_safety", "status": "fail",
                "detail": f"check raised {type(e).__name__}: {e}",
                "data": {}}


def check_credentials(exchange: str | None) -> dict:
    if not exchange:
        return {"name": "credentials", "status": "warn",
                "detail": "no --exchange specified, skipping",
                "data": {}}
    try:
        from shared.execution.credentials import load_credentials
        key, secret = load_credentials(exchange)
        if not key or not secret:
            return {"name": "credentials", "status": "fail",
                    "detail": f"empty key/secret for {exchange}",
                    "data": {"exchange": exchange}}
        return {"name": "credentials", "status": "pass",
                "detail": f"{exchange}: key len={len(key)} secret len={len(secret)}",
                "data": {"exchange": exchange, "key_len": len(key), "secret_len": len(secret)}}
    except Exception as e:
        return {"name": "credentials", "status": "fail",
                "detail": f"load_credentials({exchange!r}) → {type(e).__name__}: {e}",
                "data": {"exchange": exchange}}


def check_reconcile() -> dict:
    newest = _newest_in_dir(RECONCILE_DIR)
    if newest is None:
        return {"name": "reconcile", "status": "fail",
                "detail": f"no reconcile logs in {RECONCILE_DIR}",
                "data": {}}
    last = _last_jsonl_entry(newest)
    if not last or "ts" not in last:
        return {"name": "reconcile", "status": "fail",
                "detail": f"empty/malformed last entry in {newest.name}",
                "data": {"file": newest.name}}
    try:
        age_h = (_utcnow() - _parse_iso(last["ts"])).total_seconds() / 3600
    except Exception:
        return {"name": "reconcile", "status": "fail",
                "detail": f"unparseable ts={last.get('ts')!r}",
                "data": {"file": newest.name}}
    n_orders = last.get("n_orders", 0)
    n_skipped = last.get("n_skipped", 0)
    status = "pass"
    notes = [f"last={age_h:.2f}h ago", f"orders={n_orders}", f"skipped={n_skipped}"]
    if age_h > RECONCILE_MAX_AGE_HOURS:
        status = "warn"
        notes.append(f"stale (>{RECONCILE_MAX_AGE_HOURS}h)")
    if n_orders == 0 and n_skipped > 5:
        status = "warn"
        notes.append("many skips and no orders — investigate notional thresholds")
    return {"name": "reconcile", "status": status,
            "detail": ", ".join(notes),
            "data": {"file": newest.name, "age_hours": age_h,
                     "n_orders": n_orders, "n_skipped": n_skipped,
                     "mode": last.get("mode")}}


def check_drift() -> dict:
    newest = _newest_in_dir(DRIFT_DIR)
    if newest is None:
        return {"name": "drift", "status": "warn",
                "detail": f"no drift logs in {DRIFT_DIR} (paper mode may not run drift_check)",
                "data": {}}
    last = _last_jsonl_entry(newest)
    if not last:
        return {"name": "drift", "status": "fail",
                "detail": f"empty last entry in {newest.name}",
                "data": {"file": newest.name}}
    try:
        age_h = (_utcnow() - _parse_iso(last["ts"])).total_seconds() / 3600
    except Exception:
        return {"name": "drift", "status": "fail",
                "detail": f"unparseable ts={last.get('ts')!r}",
                "data": {}}
    severity = (last.get("severity") or "?").lower()
    status = {"ok": "pass", "warn": "warn", "critical": "fail"}.get(severity, "warn")
    if age_h > DRIFT_MAX_AGE_HOURS:
        status = "warn" if status == "pass" else status
    return {"name": "drift", "status": status,
            "detail": f"severity={severity}, age={age_h:.2f}h, max_drift={last.get('max_drift', '?')}",
            "data": {"file": newest.name, "age_hours": age_h, "severity": severity,
                     "max_drift": last.get("max_drift"), "mode": last.get("mode")}}


def check_halt_flag() -> dict:
    if HALT_FLAG.exists():
        try:
            info = json.loads(HALT_FLAG.read_text())
        except Exception:
            info = {}
        return {"name": "halt_flag", "status": "fail",
                "detail": f"halt.flag PRESENT — entries blocked. reason={info.get('reason','?')}",
                "data": info}
    return {"name": "halt_flag", "status": "pass",
            "detail": "halt.flag absent",
            "data": {}}


def check_ramp() -> dict:
    """Cross-check config ramp.factor matches ramp_state.json's last_action."""
    try:
        from shared.execution.mode import get_ramp_factor
        cfg = _load_json(REPO_ROOT / "config" / "execution_mode.json") or {}
        ramp_cfg = (cfg.get("ramp") or {})
        cfg_factor = float(ramp_cfg.get("factor", 0.0))
        runtime_factor = get_ramp_factor()  # 0 in non-live modes
        state = _load_json(RAMP_STATE_PATH) or {}
        last_action = (state.get("last_action") or {}).get("new_factor")
        notes = [f"config={cfg_factor:.2f}", f"runtime={runtime_factor:.2f}"]
        status = "pass"
        if last_action is not None and abs(float(last_action) - cfg_factor) > 1e-9:
            status = "warn"
            notes.append(f"state.last_action.new_factor={last_action} ≠ config")
        if cfg_factor not in (ramp_cfg.get("stages") or [cfg_factor]):
            status = "warn"
            notes.append("factor not in declared stages")
        return {"name": "ramp", "status": status,
                "detail": ", ".join(notes),
                "data": {"config_factor": cfg_factor,
                         "runtime_factor": runtime_factor,
                         "last_action": state.get("last_action")}}
    except Exception as e:
        return {"name": "ramp", "status": "warn",
                "detail": f"check raised {type(e).__name__}: {e}",
                "data": {}}


def check_soak_verdict(require_live: bool) -> dict:
    state = _load_json(LOOP_STATE_PATH) or {}
    verdict = state.get("soak_verdict")
    if not verdict:
        status = "fail" if require_live else "warn"
        return {"name": "soak_verdict", "status": status,
                "detail": "soak in progress (no terminal verdict yet)",
                "data": {"iteration_count": state.get("iteration_count")}}
    v_status = (verdict.get("status") or "").lower()
    if v_status == "pass":
        return {"name": "soak_verdict", "status": "pass",
                "detail": f"PASS at iter={verdict.get('iter')}",
                "data": verdict}
    if v_status == "fail":
        return {"name": "soak_verdict", "status": "fail",
                "detail": "soak FAILED — investigate before live",
                "data": verdict}
    return {"name": "soak_verdict", "status": "warn",
            "detail": f"unknown verdict status={v_status!r}",
            "data": verdict}


def check_alpha_health() -> dict:
    state = _load_json(LOOP_STATE_PATH) or {}
    health = state.get("alpha_health") or {}
    if not health:
        return {"name": "alpha_health", "status": "warn",
                "detail": "no alpha_health snapshot — run alpha_health_daily.py",
                "data": {}}
    crit = []
    warn_only = []
    for sym, e in health.items():
        streak = int(e.get("consecutive_fail_days", 0))
        if streak >= 14:
            crit.append(f"{sym}({streak}d)")
        elif streak >= 7:
            warn_only.append(f"{sym}({streak}d)")
    if crit:
        return {"name": "alpha_health", "status": "fail",
                "detail": f"critical streaks: {','.join(crit)}",
                "data": health}
    if warn_only:
        return {"name": "alpha_health", "status": "warn",
                "detail": f"warn streaks: {','.join(warn_only)}",
                "data": health}
    return {"name": "alpha_health", "status": "pass",
            "detail": f"{len(health)} symbols, no fail streaks",
            "data": health}


def check_telegram() -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat:
        return {"name": "telegram", "status": "pass",
                "detail": f"token len={len(token)}, chat configured",
                "data": {"token_len": len(token), "chat_set": True}}
    return {"name": "telegram", "status": "warn",
            "detail": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — alerts won't send",
            "data": {"token_set": bool(token), "chat_set": bool(chat)}}


# ---------------- composition ----------------

def run_all(exchange: str | None, require_live: bool) -> dict:
    checks = [
        check_execution_mode(),
        check_live_safety(require_live),
        check_credentials(exchange),
        check_reconcile(),
        check_drift(),
        check_halt_flag(),
        check_ramp(),
        check_soak_verdict(require_live),
        check_alpha_health(),
        check_telegram(),
    ]
    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    overall = "PASS" if n_fail == 0 and n_warn == 0 else ("WARN" if n_fail == 0 else "FAIL")
    return {
        "ts": _utcnow().isoformat().replace("+00:00", "Z"),
        "overall": overall,
        "n_pass": sum(1 for c in checks if c["status"] == "pass"),
        "n_warn": n_warn,
        "n_fail": n_fail,
        "require_live": require_live,
        "exchange": exchange,
        "checks": checks,
    }


def render_text(report: dict) -> str:
    icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}
    lines = [
        f"# Pre-flight check — {report['overall']}",
        f"  ts={report['ts']}  pass={report['n_pass']} warn={report['n_warn']} fail={report['n_fail']}"
        + (f"  require_live={report['require_live']}" if report['require_live'] else "")
        + (f"  exchange={report['exchange']}" if report['exchange'] else ""),
        "",
    ]
    for c in report["checks"]:
        lines.append(f"  {icon.get(c['status'], '?')} [{c['status'].upper():4}] {c['name']:<16} {c['detail']}")
    return "\n".join(lines)


def push_to_telegram(report: dict) -> None:
    try:
        from shared.notifications.telegram import TelegramNotifier, AlertLevel
        notifier = TelegramNotifier()
        if not notifier.enabled:
            print("  → telegram: not configured, skip")
            return
        icon = {"PASS": AlertLevel.INFO, "WARN": AlertLevel.WARNING, "FAIL": AlertLevel.CRITICAL}
        lines = [f"{icon[report['overall']]} <b>Pre-flight: {report['overall']}</b>",
                 f"<i>{report['ts']}</i>",
                 f"pass={report['n_pass']} warn={report['n_warn']} fail={report['n_fail']}"]
        problems = [c for c in report["checks"] if c["status"] in ("warn", "fail")]
        if problems:
            lines.append("")
            for c in problems[:8]:
                lines.append(f"• [{c['status'].upper()}] {c['name']}: {c['detail']}")
        notifier.send("\n".join(lines))
        print("  → telegram: sent")
    except Exception as e:
        print(f"  → telegram skipped ({type(e).__name__}: {e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Live pre-flight check")
    parser.add_argument("--exchange", choices=["binance", "upbit"],
                        help="Exchange to check credentials for")
    parser.add_argument("--require-live", action="store_true",
                        help="Treat 'paper mode' / 'no soak verdict' as fail (use right before flipping live)")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--push-telegram", action="store_true",
                        help="Push the report to Telegram (always on FAIL, optional on WARN/PASS)")
    args = parser.parse_args()

    report = run_all(args.exchange, args.require_live)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(report))

    if args.push_telegram and report["overall"] in ("FAIL", "WARN"):
        push_to_telegram(report)
    elif args.push_telegram:
        # PASS — only send if explicitly asked (require_live)
        if args.require_live:
            push_to_telegram(report)

    return {"PASS": 0, "WARN": 1, "FAIL": 2}.get(report["overall"], 2)


if __name__ == "__main__":
    sys.exit(main())
