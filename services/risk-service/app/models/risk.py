from pydantic import BaseModel


class RiskApprovalRequest(BaseModel):
    user_id: str | None = None
    asset: str
    requested_notional: float
    max_notional: float
    current_drawdown: float
    current_exposure: float = 0.0
    exposure_limit: float = 1.0
    automation_enabled: bool = True


class RiskApprovalResponse(BaseModel):
    approved: bool
    reason: str
    level: str
    exposure_ratio: float = 0.0
