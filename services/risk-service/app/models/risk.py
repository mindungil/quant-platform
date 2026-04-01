from datetime import UTC, datetime

from pydantic import BaseModel, Field


class RiskApprovalRequest(BaseModel):
    user_id: str | None = None
    asset: str
    requested_notional: float
    max_notional: float
    current_drawdown: float
    current_exposure: float = 0.0
    exposure_limit: float = 1.0
    automation_enabled: bool = True
    correlation_id: str | None = None


class RiskApprovalResponse(BaseModel):
    approved: bool
    reason: str
    level: str
    exposure_ratio: float = 0.0


class RiskIncident(BaseModel):
    user_id: str | None = None
    asset: str
    level: str
    approved: bool
    reason: str
    requested_notional: float
    exposure_ratio: float = 0.0
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
