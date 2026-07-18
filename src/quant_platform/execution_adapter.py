"""Reference adapter for running one batch alpha through two execution models."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import isfinite

from .backtest import BacktestConfig, BacktestResult, BacktestRunner
from .contracts import BatchAlphaPlugin, MarketBar
from .execution_engine import (
    CashFlowKind,
    CashSettlementMode,
    EventSourcedExecutionEngine,
    ExecutionEvent,
    ExecutionState,
)
from .finance import ExecutionOrderType, OrderSide

ZERO = Decimal("0")
TEN_THOUSAND = Decimal("10000")


@dataclass(frozen=True, slots=True)
class ReferenceExecutionConfig:
    initial_equity: Decimal = Decimal("1")
    fee_bps: Decimal = ZERO
    slippage_bps: Decimal = ZERO
    periods_per_year: int = 24 * 365
    account_id: str = "reference-account"
    venue: str = "reference-venue"
    settlement_currency: str = "USD"

    def __post_init__(self) -> None:
        _require_positive(self.initial_equity, "initial_equity")
        _require_non_negative(self.fee_bps, "fee_bps")
        _require_non_negative(self.slippage_bps, "slippage_bps")
        if self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")
        for name in ("account_id", "venue", "settlement_currency"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")

    @property
    def fee_rate(self) -> Decimal:
        return self.fee_bps / TEN_THOUSAND

    @property
    def slippage_rate(self) -> Decimal:
        return self.slippage_bps / TEN_THOUSAND

    def vectorized_config(self) -> BacktestConfig:
        return BacktestConfig(
            initial_equity=float(self.initial_equity),
            fee_bps=float(self.fee_bps),
            slippage_bps=float(self.slippage_bps),
            periods_per_year=self.periods_per_year,
        )


@dataclass(frozen=True, slots=True)
class EventDrivenBacktestPoint:
    period_start: datetime
    period_end: datetime
    target_position: Decimal
    ending_cash: Decimal
    ending_position_quantity: Decimal
    ending_equity: Decimal


@dataclass(frozen=True, slots=True)
class EventDrivenBacktestResult:
    plugin_name: str
    symbol: str
    config: ReferenceExecutionConfig
    target_positions: tuple[Decimal, ...]
    points: tuple[EventDrivenBacktestPoint, ...]
    events: tuple[ExecutionEvent, ...]
    final_state: ExecutionState
    ending_equity: Decimal
    ending_target_position: Decimal


@dataclass(frozen=True, slots=True)
class ReferenceExecutionComparison:
    vectorized: BacktestResult
    event_driven: EventDrivenBacktestResult


@dataclass(frozen=True, slots=True)
class _FrozenPositionPlugin:
    name: str
    positions: tuple[float, ...]

    def generate_positions(self, bars: Sequence[MarketBar]) -> tuple[float, ...]:
        if len(bars) != len(self.positions):
            raise ValueError("frozen target artifact length does not match market bars")
        return self.positions


class ReferenceExecutionAdapter:
    """Run one target-position artifact through vector and event execution paths."""

    def __init__(self, config: ReferenceExecutionConfig | None = None) -> None:
        self.config = config or ReferenceExecutionConfig()

    def run(
        self,
        plugin: BatchAlphaPlugin,
        bars: Sequence[MarketBar],
    ) -> ReferenceExecutionComparison:
        market_bars = tuple(bars)
        _validate_bars(market_bars)
        raw_positions = tuple(float(value) for value in plugin.generate_positions(market_bars))
        positions = tuple(Decimal(str(value)) for value in raw_positions)
        _validate_positions(positions, len(market_bars))
        frozen = _FrozenPositionPlugin(plugin.name, raw_positions)
        vectorized = BacktestRunner(self.config.vectorized_config()).run(frozen, market_bars)
        event_driven = self._run_positions(plugin.name, positions, market_bars)
        return ReferenceExecutionComparison(vectorized, event_driven)

    def run_event_driven(
        self,
        plugin: BatchAlphaPlugin,
        bars: Sequence[MarketBar],
    ) -> EventDrivenBacktestResult:
        market_bars = tuple(bars)
        _validate_bars(market_bars)
        positions = tuple(Decimal(str(value)) for value in plugin.generate_positions(market_bars))
        _validate_positions(positions, len(market_bars))
        return self._run_positions(plugin.name, positions, market_bars)

    def _run_positions(
        self,
        plugin_name: str,
        positions: tuple[Decimal, ...],
        market_bars: tuple[MarketBar, ...],
    ) -> EventDrivenBacktestResult:
        engine = EventSourcedExecutionEngine()
        first_time = market_bars[0].timestamp
        engine.adjust_cash(
            event_id="reference-initial-cash",
            occurred_at=first_time,
            account_id=self.config.account_id,
            currency=self.config.settlement_currency,
            amount=self.config.initial_equity,
            kind=CashFlowKind.DEPOSIT,
        )
        points: list[EventDrivenBacktestPoint] = []

        for index in range(len(market_bars) - 1):
            current = market_bars[index]
            following = market_bars[index + 1]
            current_price = Decimal(str(current.open))
            next_price = Decimal(str(following.open))
            current_position = _position_quantity(
                engine.state,
                self.config.account_id,
                current.symbol,
            )
            current_equity = _equity(
                engine.state,
                account_id=self.config.account_id,
                currency=self.config.settlement_currency,
                symbol=current.symbol,
            )
            target = positions[index]
            desired_quantity = target * current_equity / current_price
            delta = desired_quantity - current_position

            if delta != ZERO:
                side = OrderSide.BUY if delta > ZERO else OrderSide.SELL
                quantity = abs(delta)
                fill_price = _slipped_price(
                    current_price,
                    side=side,
                    slippage_rate=self.config.slippage_rate,
                )
                fee_amount = quantity * fill_price * self.config.fee_rate
                order_id = f"reference-order-{index}"
                event_prefix = f"reference-{index}"
                engine.submit_order(
                    event_id=f"{event_prefix}-submit",
                    occurred_at=current.timestamp,
                    order_id=order_id,
                    intent_id=f"reference-intent-{index}",
                    account_id=self.config.account_id,
                    venue=self.config.venue,
                    symbol=current.symbol,
                    side=side,
                    quantity=quantity,
                    order_type=ExecutionOrderType.MARKET,
                )
                engine.accept_order(
                    event_id=f"{event_prefix}-accept",
                    occurred_at=current.timestamp,
                    order_id=order_id,
                )
                engine.record_fill(
                    event_id=f"{event_prefix}-fill-event",
                    fill_id=f"reference-fill-{index}",
                    occurred_at=current.timestamp,
                    order_id=order_id,
                    quantity=quantity,
                    price=fill_price,
                    settlement_currency=self.config.settlement_currency,
                    settlement_mode=CashSettlementMode.DERIVATIVE_PNL_ONLY,
                    fee_amount=fee_amount,
                )
                if _position_quantity(
                    engine.state,
                    self.config.account_id,
                    current.symbol,
                ) != ZERO:
                    engine.mark_price(
                        event_id=f"{event_prefix}-execution-mark",
                        occurred_at=current.timestamp,
                        account_id=self.config.account_id,
                        symbol=current.symbol,
                        price=current_price,
                    )

            ending_quantity = _position_quantity(
                engine.state,
                self.config.account_id,
                current.symbol,
            )
            if ending_quantity != ZERO:
                engine.mark_price(
                    event_id=f"reference-{index}-period-mark",
                    occurred_at=following.timestamp,
                    account_id=self.config.account_id,
                    symbol=current.symbol,
                    price=next_price,
                )
            ending_equity = _equity(
                engine.state,
                account_id=self.config.account_id,
                currency=self.config.settlement_currency,
                symbol=current.symbol,
            )
            points.append(
                EventDrivenBacktestPoint(
                    period_start=current.timestamp,
                    period_end=following.timestamp,
                    target_position=target,
                    ending_cash=_cash_balance(
                        engine.state,
                        self.config.account_id,
                        self.config.settlement_currency,
                    ),
                    ending_position_quantity=ending_quantity,
                    ending_equity=ending_equity,
                )
            )

        final_target = positions[-2]
        return EventDrivenBacktestResult(
            plugin_name=plugin_name,
            symbol=market_bars[0].symbol,
            config=self.config,
            target_positions=positions,
            points=tuple(points),
            events=engine.events,
            final_state=engine.state,
            ending_equity=points[-1].ending_equity,
            ending_target_position=final_target,
        )


def _equity(
    state: ExecutionState,
    *,
    account_id: str,
    currency: str,
    symbol: str,
) -> Decimal:
    cash = _cash_balance(state, account_id, currency)
    unrealized = ZERO
    for position in state.positions:
        if position.account_id == account_id and position.symbol == symbol:
            unrealized = position.unrealized_pnl
            break
    return cash + unrealized


def _cash_balance(state: ExecutionState, account_id: str, currency: str) -> Decimal:
    for balance in state.cash:
        if balance.account_id == account_id and balance.currency == currency:
            return balance.balance
    return ZERO


def _position_quantity(state: ExecutionState, account_id: str, symbol: str) -> Decimal:
    for position in state.positions:
        if position.account_id == account_id and position.symbol == symbol:
            return position.quantity
    return ZERO


def _slipped_price(
    price: Decimal,
    *,
    side: OrderSide,
    slippage_rate: Decimal,
) -> Decimal:
    multiplier = Decimal("1") + slippage_rate
    if side is OrderSide.SELL:
        multiplier = Decimal("1") - slippage_rate
    slipped = price * multiplier
    _require_positive(slipped, "slipped price")
    return slipped


def _validate_bars(bars: tuple[MarketBar, ...]) -> None:
    if len(bars) < 2:
        raise ValueError("at least two market bars are required")
    symbol = bars[0].symbol
    previous: datetime | None = None
    for bar in bars:
        if bar.symbol != symbol:
            raise ValueError("all market bars must belong to one symbol")
        if previous is not None and bar.timestamp <= previous:
            raise ValueError("market bar timestamps must be strictly increasing")
        previous = bar.timestamp
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise ValueError("market bar timestamps must be timezone-aware")
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if any(not isfinite(value) for value in values):
            raise ValueError("market bar values must be finite")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise ValueError("market bar prices must be positive")


def _validate_positions(positions: tuple[Decimal, ...], expected: int) -> None:
    if len(positions) != expected:
        raise ValueError("batch alpha must return one target per market bar")
    if any(
        not value.is_finite() or value < Decimal("-1") or value > Decimal("1")
        for value in positions
    ):
        raise ValueError("target positions must be finite and between -1 and 1")


def _require_positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= ZERO:
        raise ValueError(f"{name} must be finite and positive")


def _require_non_negative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be finite and non-negative")
