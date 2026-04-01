from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import approve_order
from app.core.config import settings
from app.db.repository import risk_repository
from app.models.risk import RiskApprovalRequest, RiskApprovalResponse, RiskIncident
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return health_payload(
        "risk-service",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/risk/approve", response_model=RiskApprovalResponse)
def approve(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    return approve_order(payload)


@router.get("/risk/settings/{user_id}")
def risk_settings(user_id: str) -> dict:
    return {
        "max_notional": 10000,
        "exposure_limit": 50000,
        "max_drawdown": 0.10,
        "warning_drawdown": 0.05,
        "automation_enabled": True,
    }


@router.get("/risk/incidents/{user_id}", response_model=list[RiskIncident])
def incidents(user_id: str, limit: int = 50) -> list[RiskIncident]:
    return risk_repository.list_for_user(user_id, limit=limit)
