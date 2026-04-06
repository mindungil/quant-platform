import hmac
import time
from hashlib import sha256

from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.db.repository import portfolio_repository
from app.models.portfolio import PositionUpdate, PortfolioSnapshot
from shared.health import check_redis, check_sql, check_tcp, health_payload

router = APIRouter()


def _require_internal_admin(
    request: Request,
    x_internal_actor_user_id: str | None,
    x_internal_admin_timestamp: str | None,
    x_internal_admin_signature: str | None,
) -> str:
    if not x_internal_actor_user_id or not x_internal_admin_timestamp or not x_internal_admin_signature:
        raise HTTPException(status_code=403, detail="missing_internal_admin_headers")
    try:
        timestamp = int(x_internal_admin_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid_internal_admin_timestamp") from exc
    if abs(int(time.time()) - timestamp) > settings.admin_header_ttl_seconds:
        raise HTTPException(status_code=403, detail="expired_internal_admin_signature")
    message = f"{x_internal_actor_user_id}:{x_internal_admin_timestamp}:{request.url.path}"
    expected = hmac.new(settings.internal_admin_secret.encode("utf-8"), message.encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(expected, x_internal_admin_signature):
        raise HTTPException(status_code=403, detail="invalid_internal_admin_signature")
    return x_internal_actor_user_id


@router.get("/health")
def health() -> dict:
    return health_payload(
        "portfolio-service",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/portfolio/fills", response_model=PortfolioSnapshot)
def apply_fill(
    payload: PositionUpdate,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> PortfolioSnapshot:
    _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)
    return portfolio_repository.apply(payload)


@router.get("/portfolio/aggregate")
def get_aggregate_portfolio() -> dict:
    """Aggregate portfolio across all users — internal orchestrator endpoint, no auth."""
    return portfolio_repository.get_aggregate()


@router.get("/portfolio/{user_id}", response_model=PortfolioSnapshot)
def get_portfolio(user_id: str) -> PortfolioSnapshot:
    return portfolio_repository.get(user_id)


@router.get("/portfolio/{user_id}/history")
def get_portfolio_history(user_id: str, limit: int = 30) -> list[dict]:
    return portfolio_repository.get_snapshot_history(user_id, limit=limit)


@router.get("/portfolio/{user_id}/positions")
def get_positions(user_id: str) -> list[dict]:
    return portfolio_repository.get_positions(user_id)


@router.post("/portfolio/{user_id}/optimize")
def optimize_portfolio(user_id: str, payload: dict = {}) -> dict:
    from app.core.optimizer import optimize_weights

    snapshot = portfolio_repository.get(user_id)
    if not snapshot.concentration:
        return {"error": "no_positions", "detail": "No positions to optimize"}

    method = payload.get("method", "max_sharpe")
    risk_free_rate = payload.get("risk_free_rate", 0.05)
    expected_returns = payload.get("expected_returns")

    result = optimize_weights(
        positions=snapshot.concentration,
        expected_returns=expected_returns,
        risk_free_rate=risk_free_rate,
        method=method,
    )
    result["current_weights"] = snapshot.concentration
    return result
