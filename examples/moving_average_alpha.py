"""Deliberately simple public example; not a production strategy."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC
from statistics import fmean

from quant_platform import MarketBar, Signal


class MovingAverageExample:
    name = "moving-average-example"

    def __init__(self, short_window: int = 3, long_window: int = 5) -> None:
        if short_window <= 0 or long_window <= short_window:
            raise ValueError("require 0 < short_window < long_window")
        self.short_window = short_window
        self.long_window = long_window

    def generate(self, bars: Sequence[MarketBar]) -> Signal:
        if len(bars) < self.long_window:
            raise ValueError("not enough bars")
        short = fmean(bar.close for bar in bars[-self.short_window :])
        long = fmean(bar.close for bar in bars[-self.long_window :])
        score = 0.0 if long == 0 else max(-1.0, min(1.0, (short / long - 1.0) * 20.0))
        timestamp = bars[-1].timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return Signal(bars[-1].symbol, score, timestamp, self.name)
