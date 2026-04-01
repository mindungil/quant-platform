from app.core.config import settings
from app.db.repository import orchestrator_repository


def build_summary() -> dict:
    summary = {
        "status": "ok",
        "services": {
            "portfolio": settings.portfolio_service_base_url,
            "statistics": settings.statistics_service_base_url,
            "risk": settings.risk_service_base_url,
        },
        "message": "orchestrator coordination summary",
    }
    snapshot = orchestrator_repository.save(summary)
    summary["snapshot_id"] = snapshot["snapshot_id"]
    return summary
