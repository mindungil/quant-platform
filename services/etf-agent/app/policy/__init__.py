"""ETF agent decision policy plugin."""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class EtfDecisionPolicy(Protocol):
    def decide(self, state: dict) -> dict: ...


class _NoopEtfDecision:
    def decide(self, state): return {"action": "HOLD", "confidence": 0.0}


_policy: EtfDecisionPolicy | None = None


def register_etf_decision_policy(p: EtfDecisionPolicy) -> None:
    global _policy; _policy = p


def get_etf_decision_policy() -> EtfDecisionPolicy:
    return _policy or _NoopEtfDecision()


load_policy("QUANT_ETF_POLICY", plugin_label="etf_agent.decision")
