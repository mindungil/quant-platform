from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

DECISIONS_TOTAL = Counter(
    "crypto_agent_decisions_total",
    "Total decision pipeline runs",
    ["action"],
)

PIPELINE_ERRORS = Counter(
    "crypto_agent_pipeline_errors_total",
    "Pipeline errors by step",
    ["step"],
)

RISK_REJECTIONS = Counter(
    "crypto_agent_risk_rejections_total",
    "Decisions rejected by risk service",
)

ORDERS_SUBMITTED = Counter(
    "crypto_agent_orders_submitted_total",
    "Orders submitted (real + shadow)",
    ["mode"],
)

PIPELINE_DURATION = Histogram(
    "crypto_agent_pipeline_duration_seconds",
    "End-to-end pipeline latency",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

AGENT_PAUSED = Gauge(
    "crypto_agent_paused",
    "1 if agent is paused, 0 otherwise",
)

LAST_SIGNAL_SCORE = Gauge(
    "crypto_agent_last_signal_score",
    "Last signal score processed",
    ["asset"],
)


def metrics_response() -> bytes:
    """Return Prometheus text exposition."""
    return generate_latest()
