#!/usr/bin/env python3
"""Daily live alpha health monitor.

Tracks per-symbol live SR vs backtest expectations and accumulates a
consecutive-fail streak in state.json. When the streak crosses an alert
threshold we push to Telegram (Phase 1 bridge); optionally we also flip
a Redis kill-switch flag the meta-engine reads.

Why daily, not per-iter:
  * SR is a noisy daily quantity; computing it hourly amplifies noise.
  * `state.alpha_health[symbol].last_check_date` is the dedup key — re-
    running the same UTC day is a no-op.

Why grace period:
  * 5-day soak (and the first ~30 days of live) doesn't contain enough
    independent observations to evaluate `live_sr / backtest_sr` in any
    meaningful way. Grace defers evaluation until SOAK_GRACE_DAYS have
    elapsed since `state.deployed_at`.

Symbols & SR fields read from snapshots:
  BTC ← btc_30d_sr,  ETH ← eth_6m_sr,  BNB ← bnb_6m_sr
(matches the field naming used by health_check.py and narrate_anomaly.py)

Usage:
  python3 scripts/live/alpha_health_daily.py
  python3 scripts/live/alpha_health_daily.py --json
  python3 scripts/live/alpha_health_daily.py --push-telegram
  python3 scripts/live/alpha_health_daily.py --auto-suspend     # sets Redis kill flag on alert
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, date as date_cls
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

STATE_PATH = Path(os.getenv("LOOP_STATE_PATH", "/home/ubuntu/quant/data/loop/state.json"))
SNAPSHOTS_PATH = Path(os.getenv("LOOP_SNAPSHOTS_PATH", "/home/ubuntu/quant/data/loop/snapshots.jsonl"))
KILL_FLAG_KEY = "SIGNAL_META_ENABLED"

FAIL_RATIO = 0.30          # live_sr < expected_sr * 0.30 → fail
ALERT_STREAK_DAYS = 7      # consecutive fail days → alert
SUSPEND_STREAK_DAYS = 14   # consecutive fail days → auto-suspend (if --auto-suspend)
SOAK_GRACE_DAYS = 7        # first N days after deployed_at: insufficient_data

# Per-symbol weight multiplier from ratio + streak. 1.0 = normal, 0.0 = full suspend.
# These are *recommendations* written to state.alpha_health[sym].weight_multiplier;
# applying them in the ensemble path is a separate integration step.
def _compute_weight_multiplier(ratio: float | None, streak: int, in_grace: bool) -> float:
    if in_grace or ratio is None:
        return 1.0
    if streak >= SUSPEND_STREAK_DAYS:
        return 0.0
    if streak >= ALERT_STREAK_DAYS:
        # Sustained underperformance — half size on top of the ratio-based clip
        base = 0.5
    elif ratio >= 0.5:
        base = 1.0
    elif ratio >= 0.3:
        base = 0.7
    elif ratio >= 0.0:
        base = 0.5
    else:
        base = 0.3
    return base

_SR_FIELDS = {
    "BTC": "btc_30d_sr",
    "ETH": "eth_6m_sr",
    "BNB": "bnb_6m_sr",
    "SOL": "sol_6m_sr",
}


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_state() -> dict:
    if not STATE_PATH.exists():
        sys.exit(f"state file not found: {STATE_PATH}")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def _write_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_snapshots() -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []
    out = []
    for line in SNAPSHOTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _live_days_since_deploy(state: dict, now: datetime) -> int | None:
    deployed_at = state.get("deployed_at") or state.get("started_at")
    if not deployed_at:
        return None
    try:
        return max(0, (now.date() - _parse_iso(deployed_at).date()).days)
    except ValueError:
        return None


def evaluate(state: dict, snaps: list[dict], today: date_cls,
             now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    expectations = state.get("backtest_expectations") or {}
    # Strip meta keys (_doc, _production_inflated, etc.)
    expectations = {k: v for k, v in expectations.items() if not k.startswith("_")}
    existing = (state.get("alpha_health") or {})
    days_live = _live_days_since_deploy(state, now)
    in_grace = (days_live is not None and days_live < SOAK_GRACE_DAYS)

    # Latest snapshot for the most recent SR readings
    latest = snaps[-1] if snaps else {}

    new_health: dict = {}
    alerts: list[dict] = []
    suspends: list[str] = []

    for sym, live_field in _SR_FIELDS.items():
        prev = existing.get(sym) or {}
        # Idempotency: if last_check_date == today, just carry forward.
        if prev.get("last_check_date") == today.isoformat():
            new_health[sym] = prev
            continue

        live_sr = latest.get(live_field)
        exp = (expectations.get(sym.lower()) or {})
        exp_sr = exp.get("sr")

        entry = dict(prev)  # carry forward streaks
        entry["symbol"] = sym
        entry["last_check_date"] = today.isoformat()
        entry["live_sr"] = live_sr
        entry["expected_sr"] = exp_sr

        if live_sr is None or exp_sr is None or float(exp_sr) <= 0:
            entry["status"] = "no_expected" if exp_sr is None or float(exp_sr) <= 0 else "no_live_sr"
            entry["ratio"] = None
            entry.setdefault("consecutive_fail_days", prev.get("consecutive_fail_days", 0))
            new_health[sym] = entry
            continue

        ratio = float(live_sr) / float(exp_sr)
        entry["ratio"] = ratio

        if in_grace:
            entry["status"] = "insufficient_data"
            entry["consecutive_fail_days"] = prev.get("consecutive_fail_days", 0)
        elif ratio < FAIL_RATIO:
            entry["status"] = "fail"
            entry["consecutive_fail_days"] = int(prev.get("consecutive_fail_days", 0)) + 1
        else:
            entry["status"] = "pass"
            entry["consecutive_fail_days"] = 0

        # Alert / suspend triggers
        streak = entry["consecutive_fail_days"]
        if entry["status"] == "fail" and streak >= ALERT_STREAK_DAYS:
            alerts.append({
                "symbol": sym,
                "live_sr": live_sr,
                "expected_sr": exp_sr,
                "ratio": ratio,
                "streak": streak,
                "severity": "critical" if streak >= SUSPEND_STREAK_DAYS else "warn",
            })
        if entry["status"] == "fail" and streak >= SUSPEND_STREAK_DAYS:
            suspends.append(sym)

        # Weight multiplier recommendation (separate from suspend boolean —
        # gives signal path a smooth dial rather than only on/off)
        entry["weight_multiplier"] = _compute_weight_multiplier(
            ratio if entry["status"] != "insufficient_data" else None,
            streak,
            in_grace,
        )

        new_health[sym] = entry

    return {
        "today": today.isoformat(),
        "days_live": days_live,
        "in_grace": in_grace,
        "alpha_health": new_health,
        "alerts": alerts,
        "suspends": suspends,
    }


def render_text(report: dict) -> str:
    lines = [
        f"# Alpha health — {report['today']} (UTC)",
        f"  days_live={report['days_live']}, in_grace={report['in_grace']}, "
        f"FAIL_RATIO={FAIL_RATIO}, ALERT_STREAK={ALERT_STREAK_DAYS}d, SUSPEND_STREAK={SUSPEND_STREAK_DAYS}d",
        "",
    ]
    for sym, entry in report["alpha_health"].items():
        live_sr = entry.get("live_sr")
        exp_sr = entry.get("expected_sr")
        ratio = entry.get("ratio")
        streak = entry.get("consecutive_fail_days", 0)
        status = entry.get("status", "?")
        wmul = entry.get("weight_multiplier", 1.0)
        live_s = f"{live_sr:+.2f}" if isinstance(live_sr, (int, float)) else "—"
        exp_s = f"{exp_sr:+.2f}" if isinstance(exp_sr, (int, float)) else "—"
        ratio_s = f"{ratio:+.2f}" if isinstance(ratio, (int, float)) else "—"
        lines.append(f"  {sym}: live {live_s}  expected {exp_s}  "
                     f"ratio {ratio_s}  streak {streak}d  → {status}  "
                     f"weight×{wmul:.2f}")
    if report["alerts"]:
        lines += ["", "## Alerts"]
        for a in report["alerts"]:
            lines.append(
                f"  ⚠ {a['symbol']}: ratio {a['ratio']:+.2f} for {a['streak']}d "
                f"(severity={a['severity']})"
            )
    if report["suspends"]:
        lines += ["", "## Auto-suspend candidates: " + ", ".join(report["suspends"])]
    return "\n".join(lines)


def _push_alerts_telegram(alerts: list[dict]) -> None:
    if not alerts:
        return
    try:
        from shared.notifications.telegram import TelegramNotifier, AlertLevel
        notifier = TelegramNotifier()
        if not notifier.enabled:
            print("  → telegram: not configured, skip")
            return
        for a in alerts:
            icon = AlertLevel.CRITICAL if a["severity"] == "critical" else AlertLevel.WARNING
            msg = (
                f"{icon} <b>Alpha health: {a['symbol']}</b>\n"
                f"live SR <b>{a['live_sr']:+.2f}</b> vs expected {a['expected_sr']:+.2f} "
                f"(ratio {a['ratio']:+.2f})\n"
                f"consecutive fail days: <b>{a['streak']}</b>"
            )
            notifier.send(msg)
        print(f"  → telegram: pushed {len(alerts)} alert(s)")
    except Exception as exc:
        print(f"  → telegram push skipped ({type(exc).__name__}: {exc})")


def _set_redis_kill_flag(symbols: list[str]) -> bool:
    if not symbols:
        return False
    try:
        from shared.persistence import RedisStore
    except Exception as exc:
        print(f"  → suspend skipped: cannot import RedisStore ({exc})")
        return False
    try:
        r = RedisStore(os.getenv("REDIS_URL", "redis://localhost:6379"))
        r.set(KILL_FLAG_KEY, "false")
        r.set(f"{KILL_FLAG_KEY}:reason",
              json.dumps({"symbols": symbols, "set_at": datetime.now(timezone.utc).isoformat()}))
        print(f"  → suspend: set {KILL_FLAG_KEY}=false (reason: {symbols})")
        return True
    except Exception as exc:
        print(f"  → suspend failed: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily live alpha health monitor")
    parser.add_argument("--date", help="UTC date YYYY-MM-DD (default: today)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--push-telegram", action="store_true",
                        help="Push alerts to Telegram (uses TELEGRAM_BOT_TOKEN/CHAT_ID)")
    parser.add_argument("--auto-suspend", action="store_true",
                        help="Set Redis SIGNAL_META_ENABLED=false on suspend-eligible symbols")
    parser.add_argument("--no-write", action="store_true",
                        help="Don't persist alpha_health back to state.json (dry-run)")
    args = parser.parse_args()

    if args.date:
        target = date_cls.fromisoformat(args.date)
    else:
        target = datetime.now(timezone.utc).date()

    state = _load_state()
    snaps = _load_snapshots()
    report = evaluate(state, snaps, target)

    # Persist alpha_health back to state
    if not args.no_write:
        state["alpha_health"] = report["alpha_health"]
        _write_state(state)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))

    if args.push_telegram:
        _push_alerts_telegram(report["alerts"])
    if args.auto_suspend:
        _set_redis_kill_flag(report["suspends"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
