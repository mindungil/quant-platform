from datetime import datetime, timezone

UTC = timezone.utc
from uuid import uuid4

from pydantic import BaseModel, Field


class ExchangeOrderRequest(BaseModel):
    user_id: str = "system"
    exchange: str
    asset: str
    side: str
    quantity: float
    requested_notional: float = 0.0
    shadow_mode: bool = False
    api_key: str | None = None
    api_secret: str | None = None
    credential_label: str | None = None
    sandbox: bool = True
    correlation_id: str | None = None


class ExchangeOrderResponse(BaseModel):
    order_id: str = Field(default_factory=lambda: str(uuid4()))
    exchange: str
    asset: str
    side: str
    quantity: float
    status: str
    shadow_mode: bool
    exchange_order_id: str | None = None
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
    fill_status: str = "NONE"
    fees: float = 0.0
    raw_exchange_status: str | None = None
    circuit_state: str = "CLOSED"
    mode: str = "shadow"
    adapter_name: str = "simulated"
    audit_id: int | None = None
    exchange_payload_signature: str | None = None
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_update_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CancelOrderRequest(BaseModel):
    user_id: str = "system"
    exchange: str
    api_key: str | None = None
    api_secret: str | None = None
    shadow_mode: bool = False


class CancelOrderResponse(BaseModel):
    order_id: str
    status: str
    exchange: str
    shadow_mode: bool


class BalanceRequest(BaseModel):
    exchange: str
    api_key: str | None = None
    api_secret: str | None = None
    shadow_mode: bool = False


class BalanceResponse(BaseModel):
    user_id: str
    exchange: str
    balances: list[dict] = Field(default_factory=list)
    shadow_mode: bool


class PositionsResponse(BaseModel):
    user_id: str
    exchange: str
    positions: list[dict] = Field(default_factory=list)
    shadow_mode: bool


class OrderbookResponse(BaseModel):
    asset: str
    exchange: str
    bids: list[list] = Field(default_factory=list)
    asks: list[list] = Field(default_factory=list)


class ExchangeAuditRecord(BaseModel):
    audit_id: int | None = None
    user_id: str
    exchange: str
    asset: str
    side: str
    quantity: float
    requested_notional: float = 0.0
    status: str
    shadow_mode: bool
    circuit_state: str
    request_payload: dict
    response_payload: dict
    correlation_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
