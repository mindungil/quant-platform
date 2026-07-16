"""Stable, implementation-agnostic contracts shared by public and private plugins."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class MarketBar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Signal:
    """A target for the next executable decision after supplied completed bars.

    ``generated_at`` identifies the newest completed market observation used to
    create the target. It does not mean the target was already active during
    that bar. The execution layer decides the earliest tradable timestamp.
    """

    symbol: str
    score: float
    generated_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not -1.0 <= self.score <= 1.0:
            raise ValueError("signal score must be between -1 and 1")


@dataclass(frozen=True, slots=True)
class PositionTarget:
    symbol: str
    weight: float


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    size_multiplier: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.size_multiplier <= 1.0:
            raise ValueError("size_multiplier must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    symbol: str
    side: str
    quantity: float
    order_type: str = "MARKET"

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@runtime_checkable
class AlphaPlugin(Protocol):
    """Contract implemented by point-in-time public and private strategies."""

    name: str

    def generate(self, bars: Sequence[MarketBar]) -> Signal:
        """Generate the next target after the supplied completed market bars.

        The returned score is intended for the next executable decision. It is
        not the position that was active from the final supplied bar's open.
        """
        ...


@runtime_checkable
class BatchAlphaPlugin(Protocol):
    """Contract for computing a complete target-position series once.

    ``positions[i]`` is the target fraction active from ``bars[i].open`` until
    ``bars[i + 1].open``. The final position is retained for inspection but
    cannot be scored until another market bar arrives.
    """

    name: str

    def generate_positions(self, bars: Sequence[MarketBar]) -> Sequence[float]:
        """Return one bounded active position for every input market bar."""
        ...
