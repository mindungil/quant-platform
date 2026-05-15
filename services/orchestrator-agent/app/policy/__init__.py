"""Orchestrator policy plugin — retrain / re-allocation decisions."""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class RetrainPolicy(Protocol):
    def decide(self, alpha_name: str, metrics: dict) -> dict: ...


class _NoopRetrain:
    def decide(self, alpha_name, metrics): return {"retrain": False, "reason": "noop"}


_policy: RetrainPolicy | None = None


def register_retrain_policy(p: RetrainPolicy) -> None:
    global _policy; _policy = p


def get_retrain_policy() -> RetrainPolicy:
    return _policy or _NoopRetrain()


load_policy("QUANT_ORCHESTRATOR_POLICY", plugin_label="orchestrator_agent.retrain")
