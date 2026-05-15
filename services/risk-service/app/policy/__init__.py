"""Risk policy plugin interface for risk-service.

VaR/CVaR computation themselves are open (standard quant academics). What's
IP here is the drawdown-gate thresholds, kill-switch triggers, and the
funding-spike heuristics. Default = permissive (no extra blocks).
"""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class DrawdownPolicy(Protocol):
    def check(self, equity_curve: list[float], user_id: str | None = None) -> dict: ...


class KillSwitchPolicy(Protocol):
    def should_trip(self, signals: dict) -> bool: ...


class _NoopDrawdown:
    def check(self, equity_curve, user_id=None) -> dict:
        return {"level": "OK", "drawdown": 0.0, "action": None}


class _NoopKillSwitch:
    def should_trip(self, signals): return False


_drawdown: DrawdownPolicy | None = None
_kill_switch: KillSwitchPolicy | None = None


def register_drawdown_policy(p: DrawdownPolicy) -> None:
    global _drawdown; _drawdown = p


def register_kill_switch_policy(p: KillSwitchPolicy) -> None:
    global _kill_switch; _kill_switch = p


def get_drawdown_policy() -> DrawdownPolicy: return _drawdown or _NoopDrawdown()
def get_kill_switch_policy() -> KillSwitchPolicy: return _kill_switch or _NoopKillSwitch()


load_policy("QUANT_RISK_POLICY", plugin_label="risk_service")
