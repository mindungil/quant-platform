#!/usr/bin/env python3
"""D7 — Drawdown monitor daemon.

Watches portfolio balance every N seconds. Computes peak-to-current
drawdown. When DD crosses configured thresholds, emits risk-hub events
that capital_tier picks up automatically:

  • DD > soft_throttle_pct → SOFT throttle (multiplier scales 1.0 → 0.5)
  • DD > kill_pct          → HARD kill (capital_tier forced to PAPER)
  • Recovered             → clear_throttle / clear_kill

Tracks peak balance in Redis so restarts don't reset to a fresh peak.

Reference impl — portfolio balance pulled from portfolio-service. Replace
with your actual balance source when wiring up live.
"""
from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, "/code")

logger = logging.getLogger("drawdown-monitor")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

INTERVAL = int(os.getenv("DRAWDOWN_MONITOR_INTERVAL_SECONDS", "60"))
SOFT_THROTTLE_DD = float(os.getenv("DRAWDOWN_SOFT_THROTTLE_PCT", "0.05"))
SOFT_THROTTLE_MULT = float(os.getenv("DRAWDOWN_SOFT_THROTTLE_MULT", "0.5"))
KILL_DD = float(os.getenv("DRAWDOWN_KILL_PCT", "0.15"))
RECOVERY_MARGIN = float(os.getenv("DRAWDOWN_RECOVERY_MARGIN", "0.01"))
USER_ID = os.getenv("DRAWDOWN_MONITOR_USER", "bootstrap")
SCOPE = os.getenv("DRAWDOWN_MONITOR_SCOPE", "global")
DRY_RUN = os.getenv("DRAWDOWN_MONITOR_DRY_RUN", "0") == "1"

_REDIS_PEAK_KEY = "risk:drawdown:peak"


def _redis():
    try:
        import redis
        return redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
            socket_timeout=2,
        )
    except Exception:
        return None


def _fetch_balance() -> float | None:
    """Pull current portfolio NAV from portfolio-service.

    D18: the bare /portfolio/{user_id} endpoint returns the persisted snapshot
    which has positions/exposure/realized_pnl but no NAV-like equity field, so
    every cycle returned no_balance. /portfolio/{user_id}/live computes
    total_value at current market prices (positions + unrealized_pnl), which
    matches what drawdown should track.
    """
    import httpx
    url = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://execution:8012")
    try:
        r = httpx.get(f"{url}/portfolio/{USER_ID}/live", timeout=3.0)
        if r.status_code != 200:
            logger.debug("balance_fetch_non_200 status=%s body=%s",
                         r.status_code, r.text[:120])
            return None
        data = r.json()
        for k in ("total_value", "total_equity", "nav", "balance"):
            if k in data and data[k] is not None:
                return float(data[k])
        return None
    except Exception as exc:
        logger.debug("balance_fetch_failed: %s", exc)
        return None


def _evaluate_and_emit(current: float, peak: float) -> dict:
    """Decide which risk event (if any) to emit based on DD."""
    from shared.risk.monitor_hub import (
        RiskEvent, clear_kill, clear_throttle, emit, is_killed, current_size_multiplier,
    )
    if peak <= 0:
        return {"status": "no_peak"}
    dd = (peak - current) / peak
    action = "none"
    if dd >= KILL_DD:
        if not is_killed(scope=SCOPE):
            if not DRY_RUN:
                emit(RiskEvent(
                    event_class="HARD",
                    reason=f"drawdown_{int(dd*100)}pct",
                    scope=SCOPE,
                    detail=f"DD={dd:.2%} ≥ kill_pct={KILL_DD:.2%} (peak=${peak:.2f}, current=${current:.2f})",
                ))
            action = "hard_kill_emitted"
    elif dd >= SOFT_THROTTLE_DD:
        if current_size_multiplier(scope=SCOPE) > SOFT_THROTTLE_MULT:
            if not DRY_RUN:
                emit(RiskEvent(
                    event_class="SOFT",
                    reason=f"drawdown_{int(dd*100)}pct_throttle",
                    scope=SCOPE,
                    multiplier=SOFT_THROTTLE_MULT,
                    detail=f"DD={dd:.2%} ≥ soft={SOFT_THROTTLE_DD:.2%}",
                ))
            action = "soft_throttle_emitted"
    elif dd <= max(SOFT_THROTTLE_DD - RECOVERY_MARGIN, 0):
        if is_killed(scope=SCOPE):
            if not DRY_RUN:
                clear_kill(scope=SCOPE)
            action = "kill_cleared"
        elif current_size_multiplier(scope=SCOPE) < 1.0:
            if not DRY_RUN:
                clear_throttle(scope=SCOPE)
            action = "throttle_cleared"
    return {
        "status": "ok",
        "current": current,
        "peak": peak,
        "drawdown_pct": dd,
        "action": action,
    }


def _run_cycle() -> dict:
    r = _redis()
    current = _fetch_balance()
    if current is None:
        return {"status": "no_balance"}
    peak = current
    if r is not None:
        raw = r.get(_REDIS_PEAK_KEY)
        try:
            peak = max(float(raw), current) if raw else current
        except (TypeError, ValueError):
            peak = current
        if not DRY_RUN:
            r.set(_REDIS_PEAK_KEY, peak)
    return _evaluate_and_emit(current, peak)


def _daemon() -> None:
    try:
        from prometheus_client import start_http_server
        port = int(os.getenv("METRICS_PORT", "9103"))
        start_http_server(port)
        logger.info("drawdown_monitor_metrics_exposed port=%s", port)
    except Exception as exc:
        logger.warning("metrics_start_failed: %s", exc)
    logger.info("drawdown_monitor_starting interval=%s soft=%.2f kill=%.2f dry_run=%s",
                 INTERVAL, SOFT_THROTTLE_DD, KILL_DD, DRY_RUN)
    while True:
        try:
            out = _run_cycle()
            if out.get("status") == "ok" and out.get("action") != "none":
                logger.warning("dd_event: %s", out)
            else:
                logger.info("cycle: %s", out)
        except Exception as exc:
            logger.exception("cycle_failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        _daemon()
    else:
        out = _run_cycle()
        logger.info("cycle: %s", out)
