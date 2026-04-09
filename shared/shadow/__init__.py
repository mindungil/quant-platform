"""Shadow trading recorder.

When a strategy is in SHADOW status, every paper-fill the order-service
generates should be recorded here. The recorder maintains rolling per-strategy
P&L, computes Sharpe / max drawdown / win rate on a rolling window, and
pushes those metrics to the strategy-registry so the SHADOW → ACTIVE
promotion gate has real numbers to evaluate.

Without this, the existing `promote_shadow_if_ready` always sees zeros and
nothing ever gets promoted out of shadow. With it, the lifecycle is closed.
"""

from shared.shadow.recorder import ShadowRecorder, ShadowFill, ShadowSnapshot

__all__ = ["ShadowRecorder", "ShadowFill", "ShadowSnapshot"]
