"""V3 metric registry — Prometheus exporters for the V3 closed-loop modules.

Lives in a separate module so it can be imported by any service that
runs the V3 modules (intelligence, strategy-lab, execution) without
forcing the lighter observability machinery to grow.

Conventions
-----------
- Metric name prefix: `quant_v3_`
- Labels kept low-cardinality: alpha_name and regime_label are
  bounded (≤ 50 alphas, ≤ 5 regimes in practice).
- All metrics module-level singletons — re-import is a no-op.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram


# ──────────────────────────────────────────────────────────────────
# Learning loop (V3 #1)
# ──────────────────────────────────────────────────────────────────


LEARNING_ALPHA_STATE_TRANSITIONS = Counter(
    "quant_v3_learning_alpha_state_transitions_total",
    "LIVE↔SHADOW transitions triggered by AlphaPauseDecider.",
    ["alpha_name", "from_state", "to_state"],
)

LEARNING_ALPHA_DSR = Gauge(
    "quant_v3_learning_alpha_dsr",
    "Latest Deflated Sharpe Ratio per alpha (rolling window).",
    ["alpha_name"],
)

LEARNING_ALPHA_STATE = Gauge(
    "quant_v3_learning_alpha_state",
    "1 = LIVE, 0 = SHADOW (gauge to plot state-over-time).",
    ["alpha_name"],
)

LEARNING_FACTOR_IC_IR = Gauge(
    "quant_v3_learning_factor_ic_ir",
    "Latest IC_IR per factor (mean/std of rolling IC).",
    ["factor_name"],
)

LEARNING_FACTOR_DECAYED = Gauge(
    "quant_v3_learning_factor_decayed",
    "1 = decayed (weight 0), 0 = active.",
    ["factor_name"],
)

LEARNING_CYCLE_DURATION = Histogram(
    "quant_v3_learning_cycle_duration_seconds",
    "Wall-clock duration of one learning loop cycle.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)


# ──────────────────────────────────────────────────────────────────
# Attribution (V3 #2)
# ──────────────────────────────────────────────────────────────────


ATTRIBUTION_ALPHA_CUMULATIVE_PNL = Gauge(
    "quant_v3_attribution_alpha_cumulative_pnl",
    "Cumulative PnL contribution per alpha (sum over series).",
    ["alpha_name"],
)

ATTRIBUTION_ALPHA_REGIME_PNL = Gauge(
    "quant_v3_attribution_alpha_regime_pnl",
    "Cumulative PnL of (alpha, regime) bucket.",
    ["alpha_name", "regime"],
)

ATTRIBUTION_ALPHA_ROLLING_SHARPE = Gauge(
    "quant_v3_attribution_alpha_rolling_sharpe",
    "Rolling 90d (or configured window) Sharpe per alpha.",
    ["alpha_name"],
)

ATTRIBUTION_DEAD_ALPHA_FLAGGED = Counter(
    "quant_v3_attribution_dead_alpha_flagged_total",
    "Count of times an alpha hit the dead-alpha gate.",
    ["alpha_name"],
)


# ──────────────────────────────────────────────────────────────────
# Maker/Taker Bandit (V3 #3)
# ──────────────────────────────────────────────────────────────────


MAKER_TAKER_DECISIONS = Counter(
    "quant_v3_maker_taker_decisions_total",
    "Per-context, per-action selection counts.",
    ["context", "action"],
)

MAKER_TAKER_REALIZED_SLIPPAGE_BP = Histogram(
    "quant_v3_maker_taker_realized_slippage_bp",
    "Distribution of realized slippage in bp per maker/taker decision.",
    ["action"],
    buckets=(-50, -20, -10, -5, -2, 0, 2, 5, 10, 20, 50, 100, 250),
)

MAKER_TAKER_ARM_MEAN_REWARD = Gauge(
    "quant_v3_maker_taker_arm_mean_reward",
    "Posterior mean reward per (context, action) — for dashboards.",
    ["context", "action"],
)


# ──────────────────────────────────────────────────────────────────
# Convenience helpers — collapse a snapshot into Gauge updates
# ──────────────────────────────────────────────────────────────────


def export_alpha_snapshot(snapshot: dict) -> None:
    """Update gauges from a `LearningLoop.snapshot_alphas()` dict."""
    for name, info in snapshot.items():
        state = info.get("state")
        dsr = info.get("dsr")
        LEARNING_ALPHA_STATE.labels(alpha_name=name).set(
            1.0 if state == "LIVE" else 0.0
        )
        if dsr is not None:
            LEARNING_ALPHA_DSR.labels(alpha_name=name).set(float(dsr))


def export_factor_snapshot(snapshot: dict) -> None:
    """Update gauges from a `LearningLoop.snapshot_factors()` dict."""
    for name, info in snapshot.items():
        ic_ir = info.get("ic_ir")
        if ic_ir is not None:
            LEARNING_FACTOR_IC_IR.labels(factor_name=name).set(float(ic_ir))
        LEARNING_FACTOR_DECAYED.labels(factor_name=name).set(
            1.0 if info.get("is_decayed") else 0.0
        )


def record_state_transition(alpha_name: str, from_state: str, to_state: str) -> None:
    LEARNING_ALPHA_STATE_TRANSITIONS.labels(
        alpha_name=alpha_name, from_state=from_state, to_state=to_state
    ).inc()


def record_maker_taker_decision(
    context: str,
    action: str,
    realized_slippage_bp: float | None = None,
) -> None:
    MAKER_TAKER_DECISIONS.labels(context=context, action=action).inc()
    if realized_slippage_bp is not None:
        MAKER_TAKER_REALIZED_SLIPPAGE_BP.labels(action=action).observe(
            float(realized_slippage_bp)
        )


def export_bandit_stats(stats: dict) -> None:
    """Update arm-mean gauges from `MakerTakerBandit.get_stats()`."""
    for context, arms in stats.items():
        for action, info in arms.items():
            MAKER_TAKER_ARM_MEAN_REWARD.labels(
                context=context, action=action
            ).set(float(info.get("mean_reward", 0.0)))


def export_attribution_report(report) -> None:
    """Update gauges from an `AttributionReport`."""
    if not hasattr(report, "per_alpha") or report.per_alpha.empty:
        return
    for alpha, row in report.per_alpha.iterrows():
        if alpha == "_overlay":
            continue
        ATTRIBUTION_ALPHA_CUMULATIVE_PNL.labels(alpha_name=alpha).set(
            float(row["cumulative_pnl"])
        )


def export_regime_attribution_report(report) -> None:
    """Update gauges from a `RegimeAttributionReport`."""
    if not hasattr(report, "cumulative_by_regime") or report.cumulative_by_regime.empty:
        return
    for alpha in report.cumulative_by_regime.index:
        for regime in report.cumulative_by_regime.columns:
            ATTRIBUTION_ALPHA_REGIME_PNL.labels(
                alpha_name=alpha, regime=str(regime)
            ).set(float(report.cumulative_by_regime.loc[alpha, regime]))


def record_dead_alpha_flag(alpha_name: str) -> None:
    ATTRIBUTION_DEAD_ALPHA_FLAGGED.labels(alpha_name=alpha_name).inc()


def export_rolling_sharpe(rolling_df) -> None:
    """Update gauges from `rolling_attribution_sharpe()` DataFrame.
    Uses the last row only — the gauge represents 'current' state."""
    if rolling_df is None or rolling_df.empty:
        return
    last = rolling_df.iloc[-1]
    for alpha, val in last.items():
        ATTRIBUTION_ALPHA_ROLLING_SHARPE.labels(alpha_name=alpha).set(float(val))
