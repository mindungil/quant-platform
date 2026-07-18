from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform import (
    EventSourcedExecutionEngine,
    MarketBar,
    ReferenceExecutionAdapter,
    ReferenceExecutionConfig,
)


class ConstantLongPlugin:
    name = "constant_long"

    def generate_positions(self, bars: Sequence[MarketBar]) -> tuple[float, ...]:
        return tuple(1.0 for _ in bars)


class AlternatingPlugin:
    name = "alternating"

    def generate_positions(self, bars: Sequence[MarketBar]) -> tuple[float, ...]:
        return tuple(1.0 if index % 2 == 0 else -1.0 for index, _ in enumerate(bars))


def _bars() -> tuple[MarketBar, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    prices = (100.0, 110.0, 121.0, 108.9, 119.79)
    return tuple(
        MarketBar(
            symbol="BTCUSDT",
            timestamp=start + timedelta(hours=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1.0,
        )
        for index, price in enumerate(prices)
    )


def test_same_plugin_matches_vectorized_no_cost_equity() -> None:
    comparison = ReferenceExecutionAdapter().run(ConstantLongPlugin(), _bars())

    assert float(comparison.event_driven.ending_equity) == pytest.approx(
        comparison.vectorized.summary.ending_equity
    )
    assert comparison.event_driven.ending_target_position == Decimal("1.0")


def test_event_path_is_deterministic_and_replayable() -> None:
    adapter = ReferenceExecutionAdapter()
    first = adapter.run_event_driven(AlternatingPlugin(), _bars())
    second = adapter.run_event_driven(AlternatingPlugin(), _bars())

    assert first == second
    replayed = EventSourcedExecutionEngine.replay(first.events)
    assert replayed.state.to_json() == first.final_state.to_json()


def test_fee_and_slippage_reduce_event_equity() -> None:
    bars = _bars()
    free = ReferenceExecutionAdapter().run_event_driven(AlternatingPlugin(), bars)
    costly = ReferenceExecutionAdapter(
        ReferenceExecutionConfig(
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("10"),
        )
    ).run_event_driven(AlternatingPlugin(), bars)

    assert costly.ending_equity < free.ending_equity
    assert len(costly.events) > len(bars)


def test_comparison_freezes_one_strategy_artifact() -> None:
    class StatefulPlugin:
        name = "stateful"

        def __init__(self) -> None:
            self.calls = 0

        def generate_positions(self, bars: Sequence[MarketBar]) -> tuple[float, ...]:
            self.calls += 1
            return tuple(0.5 for _ in bars)

    plugin = StatefulPlugin()
    comparison = ReferenceExecutionAdapter().run(plugin, _bars())

    assert plugin.calls == 1
    assert comparison.event_driven.target_positions == tuple(
        Decimal("0.5") for _ in _bars()
    )
    assert comparison.vectorized.summary.ending_position == 0.5


def test_invalid_target_length_fails() -> None:
    class BrokenPlugin:
        name = "broken"

        def generate_positions(self, bars: Sequence[MarketBar]) -> tuple[float, ...]:
            del bars
            return (0.0,)

    with pytest.raises(ValueError, match="one target"):
        ReferenceExecutionAdapter().run_event_driven(BrokenPlugin(), _bars())
