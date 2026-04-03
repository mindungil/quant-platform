from datetime import UTC, datetime

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    strategy_id: str
    weights: dict[str, float]
    sample_size: int = 500
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
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    payoff_ratio: float = 0.0
    total_commission: float = 0.0
    out_of_sample_sharpe: float = 0.0
    # Extended trade metrics
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    statistical_validation: dict = Field(default_factory=dict)
    status: str


class BacktestJob(BaseModel):
    job_id: str
    strategy_id: str
    status: str = "PENDING"  # PENDING | RUNNING | COMPLETED | FAILED
    result: BacktestResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
