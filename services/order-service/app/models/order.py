from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


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
    strategy_id: str | None = None
    strategy_status: str = "ACTIVE"
    live_trading_requested: bool = False
    correlation_id: str | None = None
    idempotency_key: str | None = None  # Client-generated, prevents duplicate orders
    api_key: str | None = None
    api_secret: str | None = None
    credential_label: str | None = None
    credential_sandbox: bool = True
    stop_loss_pct: float | None = None       # e.g. 0.02 = 2% below entry
    take_profit_pct: float | None = None     # e.g. 0.05 = 5% above entry
    trailing_stop_pct: float | None = None   # e.g. 0.03 = 3% trailing


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
    user_id: str
    order_id: str = Field(default_factory=lambda: str(uuid4()))
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
    lifecycle: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExecutionConfig(BaseModel):
    live_trading_enabled: bool = False
    allowed_exchanges: list[str] = Field(default_factory=lambda: ["binance"])
    default_shadow_mode: bool = True
    strict_runtime: bool = False
    updated_by: str | None = None
    updated_at: datetime | None = None
    preflight_passed_at: datetime | None = None


class PreFlightCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class PreFlightResult(BaseModel):
    passed: bool
    checks: list[PreFlightCheck]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EmergencyStopResult(BaseModel):
    stopped: bool
    cancelled_orders: int = 0
    detail: str = ""


class ProtectiveOrder(BaseModel):
    order_id: str           # parent order ID
    user_id: str
    asset: str
    side: str               # opposite of parent (SELL for BUY parent)
    trigger_type: str       # STOP_LOSS | TAKE_PROFIT | TRAILING_STOP
    trigger_price: float
    quantity: float
    status: str = "ACTIVE"  # ACTIVE | TRIGGERED | CANCELLED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    highest_price: float | None = None  # for trailing stop tracking
    trailing_stop_pct: float | None = None  # needed for recalculating trailing trigger


class ProtectionCheckRequest(BaseModel):
    asset: str
    current_price: float
