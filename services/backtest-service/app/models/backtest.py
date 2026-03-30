from pydantic import BaseModel


class BacktestRequest(BaseModel):
    strategy_id: str
    weights: dict[str, float]
    sample_size: int = 365


class BacktestResult(BaseModel):
    strategy_id: str
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    status: str
