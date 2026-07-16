from datetime import UTC, datetime, timedelta

import pytest

from quant_platform import (
    BacktestConfig,
    BacktestRunner,
    MarketBar,
)


class StubBatchAlpha:
    name = "stub"

    def __init__(self, positions: list[float]) -> None:
        self._positions = positions

    def generate_positions(self, bars):
        return tuple(self._positions)


def _bars(opens: list[float]) -> list[MarketBar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    result = []
    for index, open_price in enumerate(opens):
        result.append(
            MarketBar(
                symbol="BTCUSDT",
                timestamp=start + timedelta(hours=index),
                open=open_price,
                high=open_price + 1.0,
                low=open_price - 1.0,
                close=open_price,
                volume=1_000.0,
            )
        )
    return result


def test_position_is_applied_from_current_open_to_next_open() -> None:
    runner = BacktestRunner(BacktestConfig(periods_per_year=1))
    result = runner.run(
        StubBatchAlpha([0.0, 1.0, 0.0]),
        _bars([100.0, 100.0, 110.0]),
    )

    assert result.points[0].target_position == 0.0
    assert result.points[1].target_position == 1.0
    assert result.points[1].asset_return == pytest.approx(0.10)
    assert result.summary.total_return == pytest.approx(0.10)
    assert result.summary.benchmark_return == pytest.approx(0.10)


def test_costs_are_charged_on_absolute_position_change() -> None:
    runner = BacktestRunner(
        BacktestConfig(
            fee_bps=10.0,
            slippage_bps=10.0,
            periods_per_year=1,
        )
    )
    result = runner.run(
        StubBatchAlpha([0.0, 1.0, -1.0, 0.0]),
        _bars([100.0, 100.0, 100.0, 100.0]),
    )

    assert result.summary.total_turnover == pytest.approx(3.0)
    assert result.summary.total_cost_fraction == pytest.approx(0.006)
    assert result.summary.total_return == pytest.approx(
        (1.0 - 0.002) * (1.0 - 0.004) - 1.0
    )
    assert result.summary.gross_total_return == pytest.approx(0.0)


def test_result_is_deterministic() -> None:
    runner = BacktestRunner(
        BacktestConfig(
            fee_bps=2.0,
            slippage_bps=3.0,
            periods_per_year=24 * 365,
        )
    )
    plugin = StubBatchAlpha([0.0, 0.5, 0.5, -0.5])
    bars = _bars([100.0, 101.0, 103.0, 102.0])

    assert runner.run(plugin, bars) == runner.run(plugin, tuple(bars))


def test_drawdown_and_ending_position_are_reported() -> None:
    result = BacktestRunner(BacktestConfig(periods_per_year=1)).run(
        StubBatchAlpha([1.0, 1.0, 1.0, 0.0]),
        _bars([100.0, 110.0, 99.0, 99.0]),
    )

    assert result.summary.ending_position == 1.0
    assert result.summary.max_drawdown == pytest.approx(0.10)


def test_runner_rejects_invalid_batch_output() -> None:
    runner = BacktestRunner()

    with pytest.raises(ValueError, match="one target position"):
        runner.run(StubBatchAlpha([0.0]), _bars([100.0, 101.0]))

    with pytest.raises(ValueError, match="between -1 and 1"):
        runner.run(StubBatchAlpha([0.0, 1.1]), _bars([100.0, 101.0]))


def test_runner_rejects_non_monotonic_bars() -> None:
    bars = _bars([100.0, 101.0])
    bars.reverse()

    with pytest.raises(ValueError, match="strictly increasing"):
        BacktestRunner().run(StubBatchAlpha([0.0, 0.0]), bars)
