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
    # New: recent daily returns for VaR calculation
    recent_daily_returns: list[float] = Field(default_factory=list)
    portfolio_value: float = 0.0


class RiskApprovalResponse(BaseModel):
    approved: bool
    reason: str
    level: str
    exposure_ratio: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    volatility_regime: str = "normal"  # low | normal | high


class RiskIncident(BaseModel):
    user_id: str | None = None
    asset: str
    level: str
    approved: bool
    reason: str
    requested_notional: float
    exposure_ratio: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RiskSettings(BaseModel):
    user_id: str
    max_notional: float = 10000.0
    exposure_limit: float = 50000.0
    warning_drawdown: float = 0.05
    liquidate_drawdown: float = 0.10
    target_volatility: float = 0.15
    max_single_asset_weight: float = 0.30
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
