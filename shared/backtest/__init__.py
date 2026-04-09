"""Self-contained backtest engine for strategy seeding and validation.

Distinct from `services/backtest-service/` which requires a running market-data
service. This engine runs in-process against any DataFrame, supports
walk-forward validation, deflated Sharpe, and produces a structured report
that the strategy-registry can persist as `backtest_results`.
"""

from shared.backtest.runner import (
    BacktestReport,
    BacktestRunner,
    CostModel,
    LIVE_THRESHOLDS,
    SEED_THRESHOLDS,
    run_backtest,
)
from shared.backtest.metrics import (
    deflated_sharpe_ratio,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
)
from shared.backtest.synthetic import (
    generate_ranging_ohlcv,
    generate_synthetic_ohlcv,
    generate_volatility_cycle_ohlcv,
)
from shared.backtest.walk_forward import WalkForwardResult, walk_forward

__all__ = [
    "BacktestReport",
    "BacktestRunner",
    "CostModel",
    "LIVE_THRESHOLDS",
    "SEED_THRESHOLDS",
    "run_backtest",
    "deflated_sharpe_ratio",
    "max_drawdown",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "generate_ranging_ohlcv",
    "generate_synthetic_ohlcv",
    "generate_volatility_cycle_ohlcv",
    "walk_forward",
    "WalkForwardResult",
]
