import os

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import approve_order, check_portfolio_risk
from app.core.config import settings
from app.db.repository import risk_repository
from app.models.risk import RiskApprovalRequest, RiskApprovalResponse, RiskIncident, RiskSettings
from shared.health import check_redis, check_sql, check_tcp, health_payload
from shared.internal_admin import verify_internal_admin_headers

router = APIRouter()


def _internal_admin_secret() -> str:
    return os.getenv("INTERNAL_ADMIN_SECRET", settings.internal_admin_secret)


def _admin_header_ttl_seconds() -> int:
    return int(os.getenv("ADMIN_HEADER_TTL_SECONDS", str(settings.admin_header_ttl_seconds)))


def _require_owner_or_internal(
    *,
    request: Request,
    user_id: str,
    x_user_id: str | None,
    x_internal_actor_user_id: str | None,
    x_internal_admin_timestamp: str | None,
    x_internal_admin_signature: str | None,
) -> None:
    is_internal = bool(verify_internal_admin_headers(
        secret=_internal_admin_secret(),
        path=str(request.url.path),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    ))
    if is_internal:
        return
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing_user_context")
    if x_user_id != user_id:
        raise HTTPException(status_code=403, detail="forbidden")


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
def risk_settings(
    user_id: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    return RiskSettings(user_id=user_id).model_dump(mode="json")


@router.put("/risk/settings/{user_id}")
def update_risk_settings(
    user_id: str,
    payload: dict,
    request: Request,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    settings = RiskSettings(user_id=user_id, **payload)
    return settings.model_dump(mode="json")


@router.get("/risk/incidents/{user_id}", response_model=list[RiskIncident])
def incidents(
    user_id: str,
    request: Request,
    limit: int = 50,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[RiskIncident]:
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    return risk_repository.list_for_user(user_id, limit=limit)


@router.get("/risk/incidents/recent", response_model=list[RiskIncident])
def recent_incidents(
    request: Request,
    limit: int = 50,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[RiskIncident]:
    verify_internal_admin_headers(
        secret=_internal_admin_secret(),
        path=str(request.url.path),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    ) or (_ for _ in ()).throw(HTTPException(status_code=403, detail="forbidden"))
    return risk_repository.list_recent(limit=limit)


class PortfolioCheckRequest(BaseModel):
    user_id: str
    total_drawdown: float = 0.0


class PortfolioCheckResponse(BaseModel):
    approved: bool
    reason: str
    restrictions: list[str] = []


@router.post("/risk/portfolio-check", response_model=PortfolioCheckResponse)
def portfolio_check(payload: PortfolioCheckRequest) -> PortfolioCheckResponse:
    result = check_portfolio_risk(payload.user_id, total_drawdown=payload.total_drawdown)
    return PortfolioCheckResponse(**result)
