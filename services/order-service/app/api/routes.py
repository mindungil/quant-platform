from fastapi import APIRouter
from app.core.engine import process_order
from app.models.order import OrderRequest, OrderResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/orders", response_model=OrderResponse)
def create_order(payload: OrderRequest) -> OrderResponse:
    return process_order(payload)
