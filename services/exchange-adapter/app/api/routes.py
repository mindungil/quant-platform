from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from app.db.repository import exchange_repository
from app.models.exchange import ExchangeAuditRecord, ExchangeOrderRequest, ExchangeOrderResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/exchange/orders", response_model=ExchangeOrderResponse)
def place_order(payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
    return exchange_repository.place(payload)


@router.get("/exchange/audit/{user_id}", response_model=list[ExchangeAuditRecord])
def audit(user_id: str, limit: int = 50) -> list[ExchangeAuditRecord]:
    return exchange_repository.list_for_user(user_id, limit=limit)
