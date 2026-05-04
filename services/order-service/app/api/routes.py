from datetime import datetime, timezone

UTC = timezone.utc

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.engine import process_order, exchange_client
from app.core.config import settings
from app.core.protection import protection_manager
from app.db.repository import order_repository
from app.models.order import (
    EmergencyStopResult,
    ExecutionConfig,
    OrderRequest,
    OrderResponse,
    PreFlightCheck,
    PreFlightResult,
    ProtectionCheckRequest,
    ProtectiveOrder,
)
from app.services.event_publisher import publisher
from shared.health import check_redis, check_sql, check_tcp, health_payload
from shared.internal_admin import require_internal_admin
from shared.logging import get_logger

_logger = get_logger("order-service")

router = APIRouter()


def _require_internal_admin(request, actor_user_id, timestamp, signature) -> str:
    """Positional wrapper around shared.require_internal_admin.

    Several admin endpoints (pre_flight_checks, enable_live_trading,
    logout_cancel, emergency_stop) call this with positional args.
    Without this wrapper they would NameError on first call — keep
    a thin shim so the calling sites can stay terse.
    """
    return require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=actor_user_id,
        timestamp=timestamp,
        signature=signature,
        ttl_seconds=getattr(settings, "admin_header_ttl_seconds", 300),
    )


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
def create_order(
    payload: OrderRequest,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> OrderResponse:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return process_order(payload)


@router.get("/orders/detail/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, x_user_id: str | None = Header(default=None)) -> OrderResponse:
    order = order_repository.get_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if x_user_id is not None and order.user_id != x_user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return order


@router.delete("/orders/{order_id}", response_model=OrderResponse)
def cancel_order(order_id: str, x_user_id: str | None = Header(default=None)) -> OrderResponse:
    order = order_repository.get_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    if x_user_id is not None and order.user_id != x_user_id:
        raise HTTPException(status_code=403, detail="forbidden")
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


@router.post("/orders/check-protections", response_model=list[ProtectiveOrder])
def check_protections(payload: ProtectionCheckRequest) -> list[ProtectiveOrder]:
    triggered = protection_manager.check_triggers(payload.asset, payload.current_price)
    if triggered:
        for t in triggered:
            _logger.info(
                "protection_triggered",
                extra={
                    "service": "order-service",
                    "order_id": t.order_id,
                    "trigger_type": t.trigger_type,
                    "trigger_price": t.trigger_price,
                    "current_price": payload.current_price,
                    "asset": payload.asset,
                },
            )
    return triggered


@router.get("/orders/protections/{order_id}", response_model=list[ProtectiveOrder])
def get_protections(order_id: str) -> list[ProtectiveOrder]:
    return protection_manager.get_protections(order_id)


@router.get("/admin/execution/config", response_model=ExecutionConfig)
def get_execution_config(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> ExecutionConfig:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return order_repository.get_execution_config()


@router.get("/admin/execution/posture")
def get_execution_posture(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    config = order_repository.get_execution_config()
    active_counts = order_repository.active_status_counts()
    active_total = sum(active_counts.values())
    return {
        "live_trading_enabled": config.live_trading_enabled,
        "default_shadow_mode": config.default_shadow_mode,
        "strict_runtime": config.strict_runtime,
        "allowed_exchanges": config.allowed_exchanges,
        "updated_by": config.updated_by,
        "updated_at": config.updated_at,
        "preflight_passed_at": config.preflight_passed_at,
        "preflight_recent": bool(
            config.preflight_passed_at
            and (datetime.now(UTC) - config.preflight_passed_at).total_seconds() <= _PREFLIGHT_TTL_SECONDS
        ),
        "active_orders_total": active_total,
        "active_orders_by_status": active_counts,
    }


@router.patch("/admin/execution/config", response_model=ExecutionConfig)
def update_execution_config(
    payload: ExecutionConfig,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> ExecutionConfig:
    actor = require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return order_repository.update_execution_config(
        live_trading_enabled=payload.live_trading_enabled,
        allowed_exchanges=payload.allowed_exchanges,
        default_shadow_mode=payload.default_shadow_mode,
        strict_runtime=payload.strict_runtime,
        updated_by=actor,
    )


# ---------------------------------------------------------------------------
# Live Trading Gate Flow
# ---------------------------------------------------------------------------

_PREFLIGHT_TTL_SECONDS = 300  # 5 minutes


def _check_credential_store(user_id: str, exchange: str) -> PreFlightCheck:
    try:
        resp = httpx.get(
            f"{settings.credential_store_base_url}/credentials/{user_id}/{exchange}",
            timeout=5.0,
        )
        if resp.status_code == 200:
            return PreFlightCheck(name="credentials", passed=True, detail=f"Credentials found for {exchange}")
        return PreFlightCheck(name="credentials", passed=False, detail=f"No credentials for {exchange} (HTTP {resp.status_code})")
    except Exception as exc:
        return PreFlightCheck(name="credentials", passed=False, detail=f"credential-store unreachable: {exc}")


def _check_exchange_adapter() -> PreFlightCheck:
    try:
        resp = httpx.get(
            f"{settings.exchange_adapter_base_url}/health",
            timeout=5.0,
        )
        if resp.status_code == 200:
            return PreFlightCheck(name="exchange_adapter", passed=True, detail="Exchange adapter healthy")
        return PreFlightCheck(name="exchange_adapter", passed=False, detail=f"Exchange adapter unhealthy (HTTP {resp.status_code})")
    except Exception as exc:
        return PreFlightCheck(name="exchange_adapter", passed=False, detail=f"exchange-adapter unreachable: {exc}")


def _check_risk_service() -> PreFlightCheck:
    try:
        resp = httpx.get(
            f"{settings.risk_service_base_url}/health",
            timeout=5.0,
        )
        if resp.status_code == 200:
            return PreFlightCheck(name="risk_service", passed=True, detail="Risk service healthy")
        return PreFlightCheck(name="risk_service", passed=False, detail=f"Risk service unhealthy (HTTP {resp.status_code})")
    except Exception as exc:
        return PreFlightCheck(name="risk_service", passed=False, detail=f"risk-service unreachable: {exc}")


def _check_active_strategy(user_id: str) -> PreFlightCheck:
    strategy_base = settings.strategy_registry_base_url
    try:
        resp = httpx.get(
            f"{strategy_base}/strategies",
            params={"status": "ACTIVE"},
            headers={"x-user-id": user_id},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            strategies = data if isinstance(data, list) else [data]
            if strategies:
                return PreFlightCheck(
                    name="active_strategy",
                    passed=True,
                    detail=f"{len(strategies)} active strategy(ies) found",
                )
        return PreFlightCheck(name="active_strategy", passed=False, detail="No active strategies found")
    except Exception as exc:
        return PreFlightCheck(name="active_strategy", passed=False, detail=f"strategy-registry unreachable: {exc}")


class PreFlightRequestBody(BaseModel):
    user_id: str = "bootstrap"
    exchange: str = "binance"


@router.post("/admin/execution/pre-flight", response_model=PreFlightResult)
def pre_flight_checks(
    body: PreFlightRequestBody,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> PreFlightResult:
    actor = _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)
    checks: list[PreFlightCheck] = []

    # 1. Verify credentials
    checks.append(_check_credential_store(body.user_id, body.exchange))

    # 2. Verify exchange adapter connectivity
    checks.append(_check_exchange_adapter())

    # 3. Verify risk parameters configured
    checks.append(_check_risk_service())

    # 4. Verify at least one strategy is ACTIVE
    checks.append(_check_active_strategy(body.user_id))

    all_passed = all(c.passed for c in checks)
    result = PreFlightResult(passed=all_passed, checks=checks)

    if all_passed:
        order_repository.set_preflight_passed()
        _logger.info(
            "preflight_passed",
            extra={"service": "order-service", "actor": actor, "event_type": "admin.preflight.passed"},
        )
    else:
        _logger.warning(
            "preflight_failed",
            extra={
                "service": "order-service",
                "actor": actor,
                "failed_checks": [c.name for c in checks if not c.passed],
                "event_type": "admin.preflight.failed",
            },
        )

    return result


@router.post("/admin/execution/enable-live", response_model=ExecutionConfig)
def enable_live_trading(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> ExecutionConfig:
    actor = _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)

    # Verify pre-flight was run recently
    config = order_repository.get_execution_config()
    if config.preflight_passed_at is None:
        raise HTTPException(status_code=428, detail="preflight_not_run")

    age = (datetime.now(UTC) - config.preflight_passed_at).total_seconds()
    if age > _PREFLIGHT_TTL_SECONDS:
        raise HTTPException(
            status_code=428,
            detail=f"preflight_expired:ran_{int(age)}s_ago:max_{_PREFLIGHT_TTL_SECONDS}s",
        )

    # Enable live trading
    updated = order_repository.update_execution_config(
        live_trading_enabled=True,
        allowed_exchanges=config.allowed_exchanges,
        default_shadow_mode=False,
        strict_runtime=config.strict_runtime,
        updated_by=actor,
    )
    _logger.info(
        "live_trading_enabled",
        extra={
            "service": "order-service",
            "actor": actor,
            "event_type": "admin.live_trading.enabled",
        },
    )
    return updated


@router.post("/orders/{user_id}/logout-cancel", response_model=EmergencyStopResult)
def logout_cancel(
    user_id: str,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> EmergencyStopResult:
    """Cancel all of one user's active non-filled orders.

    Called by auth-service on logout. The user's session is gone, so they
    can't manually intervene; leaving live orders open with no UI to
    cancel them is unsafe. Idempotent — repeated calls just find zero
    active orders the second time.

    NOT a kill switch — only this user is affected. Per-user execution
    mode/automation flags are managed elsewhere (PATCH /auth/me/automation).
    """
    actor = _require_internal_admin(
        request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature
    )
    user_orders = [
        o for o in order_repository.list_for_user(user_id)
        if o.status in ("PENDING", "APPROVED", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED")
    ]
    cancelled = 0
    for order in user_orders:
        try:
            try:
                exchange_client.cancel(order.order_id, order.user_id, order.exchange)
            except Exception:
                _logger.warning(
                    "logout_cancel_exchange_failed",
                    extra={"order_id": order.order_id, "user_id": user_id, "actor": actor},
                )
            order_repository.update_status(order.order_id, "CANCELLED", detail="user_logout")
            publisher.publish_order_cancelled(order.order_id, user_id)
            cancelled += 1
        except Exception:
            _logger.exception(
                "logout_cancel_error",
                extra={"order_id": order.order_id, "user_id": user_id},
            )
    _logger.info(
        "logout_cancel_executed",
        extra={"user_id": user_id, "actor": actor, "cancelled_orders": cancelled},
    )
    return EmergencyStopResult(
        stopped=True,
        cancelled_orders=cancelled,
        detail=f"User {user_id}: {cancelled} order(s) cancelled on logout",
    )


@router.post("/admin/execution/emergency-stop", response_model=EmergencyStopResult)
def emergency_stop(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> EmergencyStopResult:
    actor = _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)

    # 1. Disable live trading immediately
    config = order_repository.get_execution_config()
    order_repository.update_execution_config(
        live_trading_enabled=False,
        allowed_exchanges=config.allowed_exchanges,
        default_shadow_mode=True,
        strict_runtime=config.strict_runtime,
        updated_by=actor,
    )

    # 2. Cancel all active non-filled orders
    active_orders = order_repository.find_active_non_filled_orders()
    cancelled_count = 0
    for order in active_orders:
        try:
            try:
                exchange_client.cancel(order.order_id, order.user_id, order.exchange)
            except Exception:
                _logger.warning(
                    "emergency_stop_exchange_cancel_failed",
                    extra={
                        "service": "order-service",
                        "order_id": order.order_id,
                        "event_type": "emergency.stop.cancel_error",
                    },
                )
            order_repository.update_status(order.order_id, "CANCELLED", detail="emergency_stop")
            publisher.publish_order_cancelled(order.order_id, order.user_id)
            cancelled_count += 1
        except Exception:
            _logger.exception(
                "emergency_stop_cancel_error",
                extra={
                    "service": "order-service",
                    "order_id": order.order_id,
                    "event_type": "emergency.stop.error",
                },
            )

    # 3. Publish emergency_stop event
    publisher.publish_emergency_stop(actor, cancelled_count)

    _logger.info(
        "emergency_stop_executed",
        extra={
            "service": "order-service",
            "actor": actor,
            "cancelled_orders": cancelled_count,
            "event_type": "admin.emergency_stop",
        },
    )

    return EmergencyStopResult(
        stopped=True,
        cancelled_orders=cancelled_count,
        detail=f"Live trading disabled, {cancelled_count} order(s) cancelled",
    )
