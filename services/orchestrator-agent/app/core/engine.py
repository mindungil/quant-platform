from app.core.config import settings


def build_summary() -> dict:
    return {
        "status": "ok",
        "services": {
            "portfolio": settings.portfolio_service_base_url,
            "statistics": settings.statistics_service_base_url,
            "risk": settings.risk_service_base_url,
        },
        "message": "orchestrator bootstrap summary",
    }
