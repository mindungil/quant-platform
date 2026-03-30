from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class ExchangeOrderRequest(BaseModel):
    exchange: str
    asset: str
    side: str
    quantity: float
    shadow_mode: bool = False


class ExchangeOrderResponse(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    exchange: str
    asset: str
    side: str
    quantity: float
    status: str
    shadow_mode: bool
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
