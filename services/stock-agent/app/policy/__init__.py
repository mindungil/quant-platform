"""Stock agent decision policy plugin."""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class StockDecisionPolicy(Protocol):
    def decide(self, state: dict) -> dict: ...


class _NoopStockDecision:
    def decide(self, state): return {"action": "HOLD", "confidence": 0.0}


_policy: StockDecisionPolicy | None = None


def register_stock_decision_policy(p: StockDecisionPolicy) -> None:
    global _policy; _policy = p


def get_stock_decision_policy() -> StockDecisionPolicy:
    return _policy or _NoopStockDecision()


load_policy("QUANT_STOCK_POLICY", plugin_label="stock_agent.decision")
