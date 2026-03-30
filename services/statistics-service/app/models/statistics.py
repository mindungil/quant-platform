from pydantic import BaseModel


class StatisticsInput(BaseModel):
    user_id: str | None = None
    order_id: str | None = None
    trade_pnls: list[float]
    expected_return: float = 0.0


class StatisticsSnapshot(BaseModel):
    user_id: str | None = None
    trade_count: int
    total_return: float
    win_rate: float
    drift_detected: bool
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
