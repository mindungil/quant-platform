from datetime import UTC, datetime
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
    circuit_state: str = "CLOSED"
    mode: str = "shadow"
    adapter_name: str = "simulated"
    audit_id: int | None = None
    exchange_payload_signature: str | None = None
    filled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
