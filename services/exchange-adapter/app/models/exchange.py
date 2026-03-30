from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ExchangeOrderRequest(BaseModel):
    user_id: str
    exchange: str
    asset: str
    side: str
    quantity: float
    requested_notional: float = 0.0
    shadow_mode: bool = False


class ExchangeOrderResponse(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    exchange: str
    asset: str
    side: str
    quantity: float
    status: str
    shadow_mode: bool
    circuit_state: str = "CLOSED"
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
