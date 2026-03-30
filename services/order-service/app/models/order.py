from pydantic import BaseModel


class OrderRequest(BaseModel):
    user_id: str
    exchange: str
    asset: str
    side: str
    quantity: float
    price: float = 0.0
    requested_notional: float
    max_notional: float
    current_drawdown: float
    current_exposure: float = 0.0
    exposure_limit: float = 1.0
    automation_enabled: bool = True
    shadow_mode: bool = False


class CredentialSnapshot(BaseModel):
    user_id: str
    exchange: str
    loaded: bool
    sandbox: bool = True
    label: str | None = None


class FillSnapshot(BaseModel):
    order_id: str
    status: str
    filled_quantity: float
    filled_price: float


class PortfolioSnapshot(BaseModel):
    user_id: str
    positions: dict[str, float]
    average_entry_prices: dict[str, float] = {}
    total_exposure: float = 0.0
    rebalance_needed: bool = False


class StatisticsSnapshot(BaseModel):
    user_id: str | None = None
    trade_count: int
    total_return: float
    win_rate: float
    drift_detected: bool
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0


class OrderResponse(BaseModel):
    order_id: str | None = None
    asset: str
    side: str
    quantity: float
    status: str
    risk_reason: str
    exchange: str
    shadow_mode: bool
    credential: CredentialSnapshot
    fill: FillSnapshot | None = None
    portfolio: PortfolioSnapshot | None = None
    statistics: StatisticsSnapshot | None = None
