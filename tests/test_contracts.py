from datetime import UTC, datetime

import pytest

from quant_platform import MarketBar, OrderIntent, PluginRegistry, Signal


class StubAlpha:
    name = "stub"

    def generate(self, bars):
        return Signal(bars[-1].symbol, 0.25, bars[-1].timestamp, self.name)


def test_signal_bounds() -> None:
    with pytest.raises(ValueError):
        Signal("BTC", 1.1, datetime.now(UTC), "test")


def test_order_validation() -> None:
    with pytest.raises(ValueError):
        OrderIntent("BTC", "HOLD", 1.0)


def test_registry_is_explicit() -> None:
    registry = PluginRegistry()
    plugin = StubAlpha()
    registry.register_alpha(plugin)
    assert registry.alpha_names() == ("stub",)
    bar = MarketBar("BTC", datetime.now(UTC), 1, 1, 1, 1, 1)
    assert registry.get_alpha("stub").generate([bar]).score == 0.25
