"""Scoring policy plugin interface for signal-service.

The default behaviour is a flat-score no-op. The IP-bearing implementation
(IC-weighted scoring, regime-adaptive weights, signal-agreement bonuses)
lives in a private plugin module pointed at by QUANT_SIGNAL_POLICY.
"""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class ScoringPolicy(Protocol):
    """The surface signal-service routes are allowed to call."""

    def score(self, features: Any, external: Any | None = None) -> dict:
        """Return {signal_score: float, direction: str, components: dict}."""
        ...


class _NoopScoringPolicy:
    """Boots-without-plugin default. Returns a neutral hold signal."""

    def score(self, features: Any, external: Any | None = None) -> dict:
        return {"signal_score": 0.0, "direction": "HOLD", "components": {}}


_policy: ScoringPolicy | None = None


def register_scoring_policy(policy: ScoringPolicy) -> None:
    """Called by private plugin modules at import time."""
    global _policy
    _policy = policy


def get_scoring_policy() -> ScoringPolicy:
    return _policy or _NoopScoringPolicy()


load_policy("QUANT_SIGNAL_POLICY", plugin_label="signal_service.scoring")
