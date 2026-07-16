"""Deterministic single-asset vector backtesting primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite, sqrt
from statistics import fmean, stdev
from typing import Sequence

from .contracts import BatchAlphaPlugin, MarketBar


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Execution and cost assumptions for a minimal single-asset backtest."""

    initial_equity: float = 1.0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    periods_per_year: int = 24 * 365

    def __post_init__(self) -> None:
        if not isfinite(self.initial_equity) or self.initial_equity <= 0:
            raise ValueError("initial_equity must be finite and positive")
        if not isfinite(self.fee_bps) or self.fee_bps < 0:
            raise ValueError("fee_bps must be finite and non-negative")
        if not isfinite(self.slippage_bps) or self.slippage_bps < 0:
            raise ValueError("slippage_bps must be finite and non-negative")
        if self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")

    @property
    def variable_cost_rate(self) -> float:
        return (self.fee_bps + self.slippage_bps) / 10_000.0


@dataclass(frozen=True, slots=True)
class BacktestPoint:
    """One scored open-to-open holding period."""

    period_start: datetime
    period_end: datetime
    target_position: float
    asset_return: float
    gross_return: float
    turnover: float
    cost_fraction: float
    net_return: float
    equity: float


@dataclass(frozen=True, slots=True)
class BacktestSummary:
    starting_equity: float
    ending_equity: float
    gross_total_return: float
    total_return: float
    benchmark_return: float
    annualized_sharpe: float
    max_drawdown: float
    total_turnover: float
    total_cost_fraction: float
    ending_position: float
    periods: int


@dataclass(frozen=True, slots=True)
class BacktestResult:
    plugin_name: str
    symbol: str
    config: BacktestConfig
    points: tuple[BacktestPoint, ...]
    summary: BacktestSummary


class BacktestRunner:
    """Run a deterministic, single-asset, open-to-open vector backtest."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        plugin: BatchAlphaPlugin,
        bars: Sequence[MarketBar],
    ) -> BacktestResult:
        market_bars = tuple(bars)
        _validate_bars(market_bars)

        positions = tuple(float(value) for value in plugin.generate_positions(market_bars))
        _validate_positions(positions, expected_length=len(market_bars))

        equity = self.config.initial_equity
        gross_equity = self.config.initial_equity
        previous_position = 0.0
        points: list[BacktestPoint] = []
        net_returns: list[float] = []
        total_turnover = 0.0
        total_cost_fraction = 0.0

        for index in range(len(market_bars) - 1):
            current_bar = market_bars[index]
            next_bar = market_bars[index + 1]
            target_position = positions[index]
            turnover = abs(target_position - previous_position)
            cost_fraction = turnover * self.config.variable_cost_rate
            asset_return = next_bar.open / current_bar.open - 1.0
            gross_return = target_position * asset_return
            net_return = gross_return - cost_fraction

            if net_return <= -1.0:
                raise ValueError(
                    "backtest equity was exhausted; a liquidation model is required"
                )

            gross_equity *= 1.0 + gross_return
            equity *= 1.0 + net_return
            points.append(
                BacktestPoint(
                    period_start=current_bar.timestamp,
                    period_end=next_bar.timestamp,
                    target_position=target_position,
                    asset_return=asset_return,
                    gross_return=gross_return,
                    turnover=turnover,
                    cost_fraction=cost_fraction,
                    net_return=net_return,
                    equity=equity,
                )
            )
            net_returns.append(net_return)
            total_turnover += turnover
            total_cost_fraction += cost_fraction
            previous_position = target_position

        equity_curve = [self.config.initial_equity]
        equity_curve.extend(point.equity for point in points)
        ending_position = points[-1].target_position

        summary = BacktestSummary(
            starting_equity=self.config.initial_equity,
            ending_equity=equity,
            gross_total_return=gross_equity / self.config.initial_equity - 1.0,
            total_return=equity / self.config.initial_equity - 1.0,
            benchmark_return=market_bars[-1].open / market_bars[0].open - 1.0,
            annualized_sharpe=_annualized_sharpe(
                net_returns,
                periods_per_year=self.config.periods_per_year,
            ),
            max_drawdown=_max_drawdown(equity_curve),
            total_turnover=total_turnover,
            total_cost_fraction=total_cost_fraction,
            ending_position=ending_position,
            periods=len(points),
        )
        return BacktestResult(
            plugin_name=plugin.name,
            symbol=market_bars[0].symbol,
            config=self.config,
            points=tuple(points),
            summary=summary,
        )


def _validate_bars(bars: tuple[MarketBar, ...]) -> None:
    if len(bars) < 2:
        raise ValueError("at least two market bars are required")

    symbol = bars[0].symbol
    previous_timestamp: datetime | None = None
    for bar in bars:
        if bar.symbol != symbol:
            raise ValueError("all market bars must belong to one symbol")
        if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
            raise ValueError("market bar timestamps must be strictly increasing")
        previous_timestamp = bar.timestamp

        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(not isfinite(value) or value <= 0 for value in prices):
            raise ValueError("market bar prices must be finite and positive")
        if bar.low > bar.high:
            raise ValueError("market bar low must not exceed high")
        if not bar.low <= bar.open <= bar.high:
            raise ValueError("market bar open must be within low and high")
        if not bar.low <= bar.close <= bar.high:
            raise ValueError("market bar close must be within low and high")
        if not isfinite(bar.volume) or bar.volume < 0:
            raise ValueError("market bar volume must be finite and non-negative")


def _validate_positions(
    positions: tuple[float, ...],
    *,
    expected_length: int,
) -> None:
    if len(positions) != expected_length:
        raise ValueError(
            "batch alpha must return one target position per market bar"
        )
    if any(not isfinite(value) or not -1.0 <= value <= 1.0 for value in positions):
        raise ValueError("target positions must be finite and between -1 and 1")


def _annualized_sharpe(
    returns: Sequence[float],
    *,
    periods_per_year: int,
) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = stdev(returns)
    if volatility == 0.0:
        return 0.0
    return fmean(returns) / volatility * sqrt(periods_per_year)


def _max_drawdown(equity_curve: Sequence[float]) -> float:
    peak = equity_curve[0]
    maximum = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        maximum = max(maximum, (peak - value) / peak)
    return maximum
