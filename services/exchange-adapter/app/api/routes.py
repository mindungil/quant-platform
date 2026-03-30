from fastapi import APIRouter
from app.db.repository import exchange_repository
from app.models.exchange import ExchangeOrderRequest, ExchangeOrderResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/exchange/orders", response_model=ExchangeOrderResponse)
def place_order(payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
    return exchange_repository.place(payload)
