"""Prometheus metrics for capital-tier state.

Module-global tier lives in shared.risk.capital_tier (process-local).
crypto-agent imports it, so this is the right process to scrape from.
Lazy refresh on /metrics request — no cost on tier transitions.

Backs the G9 (capital-tier drift) dashboard.
"""

from __future__ import annotations

from prometheus_client import Gauge

from shared.risk import capital_tier

_TIER_ORDER = ("PAPER", "MICRO", "SMALL", "MID", "FULL")

capital_tier_active = Gauge(
    "capital_tier_active",
    "Numeric capital tier (PAPER=0, MICRO=1, SMALL=2, MID=3, FULL=4).",
)
capital_tier_max_order_notional_usd = Gauge(
    "capital_tier_max_order_notional_usd",
    "Active tier's max per-order notional cap (USD).",
)
capital_tier_max_daily_notional_usd = Gauge(
    "capital_tier_max_daily_notional_usd",
    "Active tier's max daily notional cap (USD).",
)


def refresh_capital_tier_metrics() -> None:
    """Pull current tier + caps and update gauges."""
    try:
        tier = capital_tier.current_tier()
        spec = capital_tier.current_spec()
        capital_tier_active.set(_TIER_ORDER.index(tier))
        capital_tier_max_order_notional_usd.set(spec.max_order_notional_usd)
        capital_tier_max_daily_notional_usd.set(spec.max_daily_notional_usd)
    except Exception:
        # Tier read must never crash a scrape.
        pass
