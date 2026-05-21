"""Prometheus metric emission for the in-process FormulaMAB instance.

Lazy refresh on /metrics scrape — values always reflect the current
arm state without paying a write cost on every MAB update().

Backs the G7 (MAB arm health) dashboard.
"""

from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import Gauge

from app.core.mab_state import formula_mab

UTC = timezone.utc

mab_arm_n = Gauge(
    "mab_arm_n",
    "Arm observation count.",
    ["arm"],
)
mab_arm_mean = Gauge(
    "mab_arm_mean",
    "Arm running mean (gamma-decayed).",
    ["arm"],
)
mab_arm_std = Gauge(
    "mab_arm_std",
    "Arm sample standard deviation.",
    ["arm"],
)
mab_arm_total_reward = Gauge(
    "mab_arm_total_reward",
    "Cumulative undiscounted reward.",
    ["arm"],
)
mab_arm_last_updated_seconds_ago = Gauge(
    "mab_arm_last_updated_seconds_ago",
    "Seconds since the arm was last updated. NaN if never updated.",
    ["arm"],
)
mab_arm_disabled = Gauge(
    "mab_arm_disabled",
    "1 if the arm is permanently disabled via MAB_DISABLED_ARMS env.",
    ["arm"],
)


def refresh_mab_metrics() -> None:
    """Sync gauges from FormulaMAB._arms. Safe if formula_mab is None."""
    if formula_mab is None:
        return
    now = datetime.now(UTC)
    arms = getattr(formula_mab, "_arms", {})
    disabled = getattr(formula_mab, "_disabled_arms", set())
    for name, arm in arms.items():
        mab_arm_n.labels(arm=name).set(arm.n)
        mab_arm_mean.labels(arm=name).set(arm.mean)
        mab_arm_std.labels(arm=name).set(arm.std)
        mab_arm_total_reward.labels(arm=name).set(arm.total_reward)
        if arm.last_updated is not None:
            ts = arm.last_updated
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            mab_arm_last_updated_seconds_ago.labels(arm=name).set((now - ts).total_seconds())
        else:
            mab_arm_last_updated_seconds_ago.labels(arm=name).set(float("nan"))
        mab_arm_disabled.labels(arm=name).set(1 if name in disabled else 0)
