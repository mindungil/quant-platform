"""Portfolio policy plugin interface for portfolio-service.

HRP / NCO / Black-Litterman remain open (academic). The IP piece is the
meta-ensemble that combines them with Kelly + drawdown overlay + signal
smoothing. Default = unweighted ensemble (equal blend).
"""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class MetaEnsemblePolicy(Protocol):
    def combine(self, signals: dict[str, float], context: dict | None = None) -> dict: ...


class _NoopMetaEnsemble:
    def combine(self, signals, context=None) -> dict:
        if not signals:
            return {"weighted_signal": 0.0, "weights": {}}
        n = len(signals)
        w = {k: 1.0 / n for k in signals}
        s = sum(v * w[k] for k, v in signals.items())
        return {"weighted_signal": s, "weights": w}


_policy: MetaEnsemblePolicy | None = None


def register_meta_ensemble_policy(p: MetaEnsemblePolicy) -> None:
    global _policy; _policy = p


def get_meta_ensemble_policy() -> MetaEnsemblePolicy:
    return _policy or _NoopMetaEnsemble()


load_policy("QUANT_PORTFOLIO_POLICY", plugin_label="portfolio_service.meta_ensemble")
