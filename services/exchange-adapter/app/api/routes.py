import os

from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.core.config import settings
from app.db.repository import exchange_repository
from app.models.exchange import (
    BalanceResponse,
    CancelOrderRequest,
    CancelOrderResponse,
    ExchangeAuditRecord,
    ExchangeOrderRequest,
    ExchangeOrderResponse,
    OrderbookResponse,
    PositionsResponse,
)
from shared.health import check_sql, health_payload
from shared.internal_admin import require_internal_admin, verify_internal_admin_headers

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
        "exchange-adapter",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/exchange/orders", response_model=ExchangeOrderResponse)
def place_order(
    payload: ExchangeOrderRequest,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> ExchangeOrderResponse:
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    return exchange_repository.place(payload)


@router.delete("/exchange/orders/{order_id}", response_model=CancelOrderResponse)
def cancel_order(
    order_id: str,
    payload: CancelOrderRequest,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> CancelOrderResponse:
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    return exchange_repository.cancel_order(order_id, payload)


@router.get("/exchange/balance/{user_id}", response_model=BalanceResponse)
def get_balance(
    request: Request,
    user_id: str,
    exchange: str,
    shadow_mode: bool = False,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> BalanceResponse:
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    # Credentials are fetched internally from credential-store; never via URL params
    return exchange_repository.get_balance(
        user_id, exchange, shadow_mode=shadow_mode,
    )


@router.get("/exchange/positions/{user_id}", response_model=PositionsResponse)
def get_positions(
    request: Request,
    user_id: str,
    exchange: str,
    shadow_mode: bool = False,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> PositionsResponse:
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    # Credentials are fetched internally from credential-store; never via URL params
    return exchange_repository.get_positions(
        user_id, exchange, shadow_mode=shadow_mode,
    )


@router.get("/exchange/orderbook/{asset}", response_model=OrderbookResponse)
def get_orderbook(
    asset: str,
    exchange: str,
    depth: int = 20,
    shadow_mode: bool = False,
) -> OrderbookResponse:
    return exchange_repository.get_orderbook(
        asset, exchange, depth=depth, shadow_mode=shadow_mode,
    )


@router.post("/exchange/credentials/{user_id}/{exchange}/verify")
def verify_credential(
    user_id: str,
    exchange: str,
    request: Request,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    """Validate that stored API credentials can authenticate with the exchange."""
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    return exchange_repository.verify_credential(user_id, exchange)


@router.get("/exchange/audit/{user_id}", response_model=list[ExchangeAuditRecord])
def audit(
    user_id: str,
    request: Request,
    limit: int = 50,
    x_user_id: str | None = Header(default=None),
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[ExchangeAuditRecord]:
    _require_owner_or_internal(
        request=request,
        user_id=user_id,
        x_user_id=x_user_id,
        x_internal_actor_user_id=x_internal_actor_user_id,
        x_internal_admin_timestamp=x_internal_admin_timestamp,
        x_internal_admin_signature=x_internal_admin_signature,
    )
    return exchange_repository.list_for_user(user_id, limit=limit)


@router.get("/exchange/orders/{order_id}/status")
def get_order_status(
    order_id: str,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> dict:
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    status = exchange_repository.get_order_status(order_id)
    if status is None:
        raise HTTPException(status_code=404, detail="order_not_found")
    return status


@router.get("/exchange/orders/{order_id}/fills")
def get_order_fills(
    order_id: str,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[dict]:
    require_internal_admin(
        request=request,
        secret=_internal_admin_secret(),
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=_admin_header_ttl_seconds(),
    )
    fills = exchange_repository.get_order_fills(order_id)
    if not fills:
        raise HTTPException(status_code=404, detail="order_fills_not_found")
    return fills


# ---------------------------------------------------------------------------
# ccxt unified adapter endpoints
# ---------------------------------------------------------------------------

@router.post("/ccxt/{exchange_id}/order")
def ccxt_place_order(exchange_id: str, payload: dict) -> dict:
    from app.adapters.ccxt_adapter import CcxtAdapter, CCXT_AVAILABLE
    if not CCXT_AVAILABLE:
        raise HTTPException(status_code=503, detail="ccxt not available")
    try:
        adapter = CcxtAdapter(
            exchange_id=exchange_id,
            api_key=payload.get("api_key", ""),
            api_secret=payload.get("api_secret", ""),
            sandbox=payload.get("sandbox", True),
        )
        return adapter.place_order(
            symbol=payload["symbol"],
            side=payload["side"],
            amount=payload["amount"],
            order_type=payload.get("order_type", "market"),
            price=payload.get("price"),
        )
    except Exception as e:
        return {"error": str(e)}


@router.get("/ccxt/{exchange_id}/ticker/{symbol}")
def ccxt_get_ticker(exchange_id: str, symbol: str) -> dict:
    from app.adapters.ccxt_adapter import CcxtAdapter, CCXT_AVAILABLE
    if not CCXT_AVAILABLE:
        raise HTTPException(status_code=503, detail="ccxt not available")
    try:
        adapter = CcxtAdapter(exchange_id=exchange_id, sandbox=True)
        return adapter.get_ticker(symbol.replace("-", "/"))
    except Exception as e:
        return {"error": str(e)}
