from pydantic import BaseModel


class OrderRequest(BaseModel):
    user_id: str
    exchange: str
    asset: str
    side: str
    quantity: float
    requested_notional: float
    max_notional: float
    current_drawdown: float
    shadow_mode: bool = False


class CredentialSnapshot(BaseModel):
    user_id: str
    exchange: str
    loaded: bool


class OrderResponse(BaseModel):
    asset: str
    side: str
    quantity: float
    status: str
    risk_reason: str
    exchange: str
    shadow_mode: bool
    credential: CredentialSnapshot
