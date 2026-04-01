from datetime import UTC, datetime

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    strategy_id: str
    weights: dict[str, float]
    sample_size: int = 365
    asset: str = "BTCUSDT"


class BacktestResult(BaseModel):
    strategy_id: str
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    total_return: float = 0.0
    trade_count: int = 0
    avg_trade_pnl: float = 0.0
    status: str


class BacktestJob(BaseModel):
    job_id: str
    strategy_id: str
    status: str = "PENDING"  # PENDING | RUNNING | COMPLETED | FAILED
    result: BacktestResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
