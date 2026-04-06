from fastapi import APIRouter, HTTPException, Response
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

router = APIRouter()


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
def place_order(payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
    return exchange_repository.place(payload)


@router.delete("/exchange/orders/{order_id}", response_model=CancelOrderResponse)
def cancel_order(order_id: str, payload: CancelOrderRequest) -> CancelOrderResponse:
    return exchange_repository.cancel_order(order_id, payload)


@router.get("/exchange/balance/{user_id}", response_model=BalanceResponse)
def get_balance(
    user_id: str,
    exchange: str,
    shadow_mode: bool = False,
) -> BalanceResponse:
    # Credentials are fetched internally from credential-store; never via URL params
    return exchange_repository.get_balance(
        user_id, exchange, shadow_mode=shadow_mode,
    )


@router.get("/exchange/positions/{user_id}", response_model=PositionsResponse)
def get_positions(
    user_id: str,
    exchange: str,
    shadow_mode: bool = False,
) -> PositionsResponse:
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


@router.get("/exchange/audit/{user_id}", response_model=list[ExchangeAuditRecord])
def audit(user_id: str, limit: int = 50) -> list[ExchangeAuditRecord]:
    return exchange_repository.list_for_user(user_id, limit=limit)


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
