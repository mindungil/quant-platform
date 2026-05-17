"""Real-time risk monitor hub — central event sink for kill switches,
drawdown alerts, vol spikes, and dead-alpha flags. Plugs into the V3
Prometheus exporters so a single Grafana panel + alertmanager rule
covers every safety condition.

Design
------
The hub is a *passive* aggregator: callers (signal_service, execution,
incubator) emit events; the hub records them as counters + gauges and
optionally routes them to a notification sink (Slack webhook, PagerDuty,
email).

Three event classes:
  • HARD KILL    — emergency stop (e.g., drawdown > kill_dd). Records
                   to quant_v3_risk_kill_total and bumps the gauge
                   quant_v3_risk_kill_active to 1.
  • SOFT THROTTLE — scale-down (vol spike, regime change). Adjusts
                    quant_v3_risk_size_multiplier ∈ [0, 1].
  • OBSERVATION  — informational (dead alpha flag, decay flip). Counter
                   only — no behavioral change, alertmanager fans out.

Pure module, no I/O on the hot path. Notification routing is a hook
(`register_notifier(callable)`) so tests don't need a webhook.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

try:
    from prometheus_client import Counter, Gauge
    _RISK_KILL_TOTAL = Counter(
        "quant_v3_risk_kill_total",
        "Hard-kill events triggered by the risk monitor.",
        ["reason", "scope"],
    )
    _RISK_KILL_ACTIVE = Gauge(
        "quant_v3_risk_kill_active",
        "1 if any hard kill is currently active; 0 otherwise.",
        ["scope"],
    )
    _RISK_SIZE_MULT = Gauge(
        "quant_v3_risk_size_multiplier",
        "Soft-throttle multiplier in [0,1] applied to all sizing.",
        ["scope"],
    )
    _RISK_EVENT_TOTAL = Counter(
        "quant_v3_risk_event_total",
        "All risk events emitted (HARD/SOFT/OBS).",
        ["event_class", "reason", "scope"],
    )
    _METRICS_AVAILABLE = True
except ImportError:
    _RISK_KILL_TOTAL = _RISK_KILL_ACTIVE = _RISK_SIZE_MULT = _RISK_EVENT_TOTAL = None  # type: ignore
    _METRICS_AVAILABLE = False


logger = logging.getLogger(__name__)

EventClass = Literal["HARD", "SOFT", "OBS"]


@dataclass
class RiskEvent:
    event_class: EventClass
    reason: str
    scope: str = "global"
    detail: str = ""
    multiplier: Optional[float] = None  # required for SOFT events
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.event_class not in ("HARD", "SOFT", "OBS"):
            raise ValueError(f"bad event_class: {self.event_class}")
        if self.event_class == "SOFT" and self.multiplier is None:
            raise ValueError("SOFT events require a multiplier in [0, 1]")
        if self.multiplier is not None:
            if not (0.0 <= self.multiplier <= 1.0):
                raise ValueError(f"multiplier must be in [0, 1], got {self.multiplier}")


# Notification sink registry (lazy — production wires Slack/PagerDuty here)
_NOTIFIERS: list[Callable[[RiskEvent], None]] = []
_LOCK = threading.Lock()


def register_notifier(fn: Callable[[RiskEvent], None]) -> None:
    """Add a notification callback. Called for every emit()."""
    with _LOCK:
        _NOTIFIERS.append(fn)


def clear_notifiers() -> None:
    with _LOCK:
        _NOTIFIERS.clear()


# Active state — one entry per (event_class, scope)
_ACTIVE_KILLS: dict[str, RiskEvent] = {}
_ACTIVE_THROTTLES: dict[str, float] = {}


def emit(event: RiskEvent) -> None:
    """Record an event + fan out to notifiers + update Prometheus."""
    with _LOCK:
        # Metric updates
        if _METRICS_AVAILABLE:
            try:
                _RISK_EVENT_TOTAL.labels(
                    event_class=event.event_class,
                    reason=event.reason,
                    scope=event.scope,
                ).inc()
                if event.event_class == "HARD":
                    _RISK_KILL_TOTAL.labels(reason=event.reason, scope=event.scope).inc()
                    _RISK_KILL_ACTIVE.labels(scope=event.scope).set(1.0)
                    _ACTIVE_KILLS[event.scope] = event
                elif event.event_class == "SOFT":
                    mult = float(event.multiplier or 0.0)
                    _RISK_SIZE_MULT.labels(scope=event.scope).set(mult)
                    _ACTIVE_THROTTLES[event.scope] = mult
            except Exception as exc:
                logger.debug("risk_metric_update_failed: %s", exc)

        # Log
        logger.warning(
            "risk_event",
            extra={
                "event_class": event.event_class,
                "reason": event.reason,
                "scope": event.scope,
                "detail": event.detail[:200],
                "multiplier": event.multiplier,
            },
        )

        # Notify
        for n in list(_NOTIFIERS):
            try:
                n(event)
            except Exception as exc:
                logger.warning("notifier_failed: %s", exc)


def clear_kill(scope: str = "global") -> None:
    """Mark a hard-kill as resolved. Operator-initiated only."""
    with _LOCK:
        _ACTIVE_KILLS.pop(scope, None)
        if _METRICS_AVAILABLE and _RISK_KILL_ACTIVE is not None:
            try:
                _RISK_KILL_ACTIVE.labels(scope=scope).set(0.0)
            except Exception:
                pass


def clear_throttle(scope: str = "global") -> None:
    """Restore size multiplier to 1.0."""
    with _LOCK:
        _ACTIVE_THROTTLES.pop(scope, None)
        if _METRICS_AVAILABLE and _RISK_SIZE_MULT is not None:
            try:
                _RISK_SIZE_MULT.labels(scope=scope).set(1.0)
            except Exception:
                pass


# ─── consumers query these ──────────────────────────────────────────


def is_killed(scope: str = "global") -> bool:
    with _LOCK:
        return scope in _ACTIVE_KILLS


def current_size_multiplier(scope: str = "global") -> float:
    """Effective sizing multiplier. 0.0 if killed, else throttle value (default 1.0)."""
    with _LOCK:
        if scope in _ACTIVE_KILLS:
            return 0.0
        return _ACTIVE_THROTTLES.get(scope, 1.0)


def snapshot() -> dict:
    """Full state — for /risk/status endpoint or Grafana table panel."""
    with _LOCK:
        return {
            "active_kills": {k: vars(v) for k, v in _ACTIVE_KILLS.items()},
            "active_throttles": dict(_ACTIVE_THROTTLES),
            "n_notifiers": len(_NOTIFIERS),
        }


# ─── built-in notifier: Slack incoming webhook ─────────────────────


def slack_webhook_notifier(webhook_url: str) -> Callable[[RiskEvent], None]:
    """Returns a notifier that POSTs to a Slack incoming webhook.

    Use:
        register_notifier(slack_webhook_notifier(os.getenv('RISK_SLACK_WEBHOOK')))
    """
    def _post(event: RiskEvent) -> None:
        try:
            import httpx
            httpx.post(
                webhook_url,
                json={
                    "text": (
                        f":rotating_light: *{event.event_class}* risk event "
                        f"[{event.scope}] — {event.reason}"
                        + (f"\n>>> {event.detail}" if event.detail else "")
                    )
                },
                timeout=3.0,
            )
        except Exception as exc:
            logger.debug("slack_notifier_failed: %s", exc)
    return _post
