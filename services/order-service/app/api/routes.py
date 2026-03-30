from fastapi import APIRouter
from app.core.engine import process_order
from app.db.repository import order_repository
from app.models.order import OrderRequest, OrderResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/orders", response_model=OrderResponse)
def create_order(payload: OrderRequest) -> OrderResponse:
    return process_order(payload)


@router.get("/orders/{user_id}", response_model=list[OrderResponse])
def list_orders(user_id: str) -> list[OrderResponse]:
    return order_repository.list_for_user(user_id)
