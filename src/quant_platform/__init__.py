"""Public contracts for the Quant Platform open-core project."""

from .backtest import (
    BacktestConfig,
    BacktestPoint,
    BacktestResult,
    BacktestRunner,
    BacktestSummary,
)
from .contracts import (
    AlphaPlugin,
    BatchAlphaPlugin,
    MarketBar,
    OrderIntent,
    PositionTarget,
    RiskDecision,
    Signal,
)
from .registry import PluginRegistry

__all__ = [
    "AlphaPlugin",
    "BacktestConfig",
    "BacktestPoint",
    "BacktestResult",
    "BacktestRunner",
    "BacktestSummary",
    "BatchAlphaPlugin",
    "MarketBar",
    "OrderIntent",
    "PluginRegistry",
    "PositionTarget",
    "RiskDecision",
    "Signal",
]
