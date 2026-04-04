from datetime import UTC, datetime

from pydantic import BaseModel, Field


class StatisticsInput(BaseModel):
    user_id: str | None = None
    strategy_id: str | None = None
    order_id: str | None = None
    asset: str | None = None
    correlation_id: str | None = None
    trade_pnls: list[float]
    expected_return: float = 0.0
    baseline_sharpe: float | None = None


class StatisticsSnapshot(BaseModel):
    user_id: str | None = None
    strategy_id: str | None = None
    trade_count: int
    total_return: float
    win_rate: float
    drift_detected: bool
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    payoff_ratio: float = 0.0
    expectancy: float = 0.0
    drift_score: float | None = None
    recent_sharpe: float | None = None
    recent_trade_pnls: list[float] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
