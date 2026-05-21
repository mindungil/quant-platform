from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.db.repository import portfolio_repository
from app.models.portfolio import PositionUpdate, PortfolioSnapshot
from shared.health import check_redis, check_sql, check_tcp, health_payload
from shared.internal_admin import require_internal_admin

router = APIRouter()


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
    try:
        from app.core.portfolio_metrics import refresh_portfolio_metrics
        refresh_portfolio_metrics()
    except Exception:
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/portfolio/fills", response_model=PortfolioSnapshot)
def apply_fill(
    payload: PositionUpdate,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> PortfolioSnapshot:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return portfolio_repository.apply(payload)


@router.get("/portfolio/aggregate")
def get_aggregate_portfolio(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    """Aggregate portfolio across all users — internal orchestrator endpoint."""
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return portfolio_repository.get_aggregate()


@router.get("/portfolio/summary")
def get_portfolio_summary(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    """Compliance-shaped summary: {equity, positions, kill_switch}.

    Consumed by signal-service's compliance provider for pre-trade checks.
    Internal endpoint, read-only but still authenticated.
    """
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return portfolio_repository.get_summary()


@router.get("/portfolio/{user_id}", response_model=PortfolioSnapshot)
def get_portfolio(user_id: str) -> PortfolioSnapshot:
    return portfolio_repository.get(user_id)


@router.get("/portfolio/{user_id}/live")
def get_portfolio_live(user_id: str) -> dict:
    """Portfolio with real-time unrealized PnL from live market prices."""
    return portfolio_repository.get_portfolio_with_live_pnl(user_id)


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
