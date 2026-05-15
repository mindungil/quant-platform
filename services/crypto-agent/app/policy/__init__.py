"""Decision policy plugin interface for crypto-agent.

Four hooks the LangGraph DAG can route through. Default = no-ops so the
graph completes without a plugin. The IP-bearing implementations
(recall, recommender, formula selector, learning scheduler) live in
private modules registered via QUANT_CRYPTO_POLICY.
"""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class RecallPolicy(Protocol):
    def recall(self, asset: str, signal_score: float, user_id: str) -> list[dict]: ...


class RecommendPolicy(Protocol):
    def recommend(self, state: dict) -> dict: ...


class FormulaSelectorPolicy(Protocol):
    def select(self, regime: str, asset: str) -> str: ...


class LearningSchedulerPolicy(Protocol):
    def should_retrain(self, alpha_name: str, since_ts: float) -> bool: ...


class _Noop:
    def recall(self, asset, signal_score, user_id): return []
    def recommend(self, state): return {"action": "HOLD", "confidence": 0.0}
    def select(self, regime, asset): return "balanced"
    def should_retrain(self, alpha_name, since_ts): return False


_recall: RecallPolicy | None = None
_recommend: RecommendPolicy | None = None
_formula: FormulaSelectorPolicy | None = None
_scheduler: LearningSchedulerPolicy | None = None


def register_recall_policy(p: RecallPolicy) -> None:
    global _recall; _recall = p


def register_recommend_policy(p: RecommendPolicy) -> None:
    global _recommend; _recommend = p


def register_formula_selector_policy(p: FormulaSelectorPolicy) -> None:
    global _formula; _formula = p


def register_learning_scheduler_policy(p: LearningSchedulerPolicy) -> None:
    global _scheduler; _scheduler = p


_NOOP = _Noop()


def get_recall_policy() -> RecallPolicy: return _recall or _NOOP
def get_recommend_policy() -> RecommendPolicy: return _recommend or _NOOP
def get_formula_selector_policy() -> FormulaSelectorPolicy: return _formula or _NOOP
def get_learning_scheduler_policy() -> LearningSchedulerPolicy: return _scheduler or _NOOP


load_policy("QUANT_CRYPTO_POLICY", plugin_label="crypto_agent")
