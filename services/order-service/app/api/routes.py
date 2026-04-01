import hmac
import time
from hashlib import sha256

from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import process_order
from app.core.config import settings
from app.db.repository import order_repository
from app.models.order import ExecutionConfig, OrderRequest, OrderResponse
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
        "order-service",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/orders", response_model=OrderResponse)
def create_order(payload: OrderRequest) -> OrderResponse:
    return process_order(payload)


@router.get("/orders/{user_id}", response_model=list[OrderResponse])
def list_orders(user_id: str) -> list[OrderResponse]:
    return order_repository.list_for_user(user_id)


@router.get("/admin/execution/config", response_model=ExecutionConfig)
def get_execution_config(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> ExecutionConfig:
    _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)
    return order_repository.get_execution_config()


@router.patch("/admin/execution/config", response_model=ExecutionConfig)
def update_execution_config(
    payload: ExecutionConfig,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> ExecutionConfig:
    actor = _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)
    return order_repository.update_execution_config(
        live_trading_enabled=payload.live_trading_enabled,
        allowed_exchanges=payload.allowed_exchanges,
        default_shadow_mode=payload.default_shadow_mode,
        strict_runtime=payload.strict_runtime,
        updated_by=actor,
    )
