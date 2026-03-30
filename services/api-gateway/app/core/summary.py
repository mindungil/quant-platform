from app.core.config import settings


def gateway_summary() -> dict:
    return {
        "signal_service": settings.signal_service_base_url,
        "portfolio_service": settings.portfolio_service_base_url,
        "statistics_service": settings.statistics_service_base_url,
        "realtime_topics": [
            "order.filled.*",
            "signal.threshold.crossed.*",
            "risk.triggered.*",
            "agent.*.action",
        ],
    }
