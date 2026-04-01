import hmac
import time
from hashlib import sha256

from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import process_order, exchange_client
from app.core.config import settings
from app.db.repository import order_repository
from app.models.order import ExecutionConfig, OrderRequest, OrderResponse
from app.services.event_publisher import publisher
from shared.health import check_redis, check_sql, check_tcp, health_payload
from shared.logging import get_logger

_logger = get_logger("order-service")

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


@router.get("/orders/detail/{order_id}", response_model=OrderResponse)
def get_order(order_id: str) -> OrderResponse:
    order = order_repository.get_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    return order


@router.delete("/orders/{order_id}", response_model=OrderResponse)
def cancel_order(order_id: str) -> OrderResponse:
    order = order_repository.get_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if order.status in ("CANCELLED", "FILLED", "REJECTED", "FAILED"):
        raise HTTPException(status_code=409, detail=f"order_already_{order.status.lower()}")

    # Attempt exchange cancellation for submitted orders
    exchange_cancel_result = None
    if order.status in ("SUBMITTED", "APPROVED", "PENDING"):
        try:
            exchange_cancel_result = exchange_client.cancel(order.order_id, order.user_id, order.exchange)
        except Exception:
            _logger.exception(
                "exchange_cancel_failed",
                extra={
                    "service": "order-service",
                    "order_id": order.order_id,
                    "user_id": order.user_id,
                    "event_type": "order.cancel.exchange_error",
                },
            )

    # Update status
    order_repository.update_status(order.order_id, "CANCELLED")
    order.status = "CANCELLED"

    # Record lifecycle event
    order_repository.record_lifecycle(
        order.order_id,
        order.user_id,
        "CANCELLED",
        detail={
            "stage": "cancellation",
            "exchange_cancel_result": exchange_cancel_result,
        },
    )

    # Publish order.cancelled event
    publisher.publish_order_cancelled(order.order_id, order.user_id)

    return order


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
