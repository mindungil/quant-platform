from pydantic import BaseModel


class RiskApprovalRequest(BaseModel):
    asset: str
    requested_notional: float
    max_notional: float
    current_drawdown: float


class RiskApprovalResponse(BaseModel):
    approved: bool
    reason: str
    level: str
