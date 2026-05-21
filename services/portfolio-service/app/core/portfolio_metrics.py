"""Prometheus metrics for portfolio aggregate state.

Refreshed lazily on /metrics scrape. Backs the G9 (capital-tier
drift) dashboard alongside capital_tier_* metrics emitted from
crypto-agent.
"""

from __future__ import annotations

from prometheus_client import Gauge

from app.db.repository import portfolio_repository

portfolio_total_exposure_usd = Gauge(
    "portfolio_total_exposure_usd",
    "Sum of |quantity| * current_price across all aggregated positions.",
)
portfolio_concentration_max_weight = Gauge(
    "portfolio_concentration_max_weight",
    "Largest single-asset weight (0..1) in the aggregate portfolio.",
)
portfolio_position_count = Gauge(
    "portfolio_position_count",
    "Number of distinct assets with a non-zero position.",
)


def refresh_portfolio_metrics() -> None:
    try:
        agg = portfolio_repository.get_aggregate()
        portfolio_total_exposure_usd.set(float(agg.get("total_exposure", 0.0)))
        conc = agg.get("concentration") or {}
        portfolio_concentration_max_weight.set(max(conc.values(), default=0.0))
        portfolio_position_count.set(len(conc))
    except Exception:
        pass
