from pydantic import BaseModel


class StatisticsInput(BaseModel):
    trade_pnls: list[float]
    expected_return: float = 0.0


class StatisticsSnapshot(BaseModel):
    trade_count: int
    total_return: float
    win_rate: float
    drift_detected: bool
