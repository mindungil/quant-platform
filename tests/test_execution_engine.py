from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.execution_engine import (
    CashFlowKind,
    CashSettlementMode,
    EngineOrderState,
    EventSourcedExecutionEngine,
)
from quant_platform.finance import ExecutionOrderType, OrderSide

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _submit(engine: EventSourcedExecutionEngine, order_id: str = "order-1") -> None:
    engine.submit_order(
        event_id=f"{order_id}-submitted",
        occurred_at=T0,
        order_id=order_id,
        intent_id=f"intent-{order_id}",
        account_id="account-1",
        venue="reference",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_type=ExecutionOrderType.MARKET,
    )


def test_spot_lifecycle_cash_position_mark_and_replay() -> None:
    engine = EventSourcedExecutionEngine()
    engine.adjust_cash(
        event_id="deposit",
        occurred_at=T0,
        account_id="account-1",
        currency="USDT",
        amount=Decimal("10000"),
        kind=CashFlowKind.DEPOSIT,
    )
    _submit(engine)
    engine.accept_order(event_id="accepted", occurred_at=T0, order_id="order-1")
    first = engine.record_fill(
        event_id="fill-event-1",
        fill_id="fill-1",
        occurred_at=T0 + timedelta(seconds=1),
        order_id="order-1",
        quantity=Decimal("4"),
        price=Decimal("100"),
        settlement_currency="USDT",
        settlement_mode=CashSettlementMode.SPOT_NOTIONAL,
        fee_amount=Decimal("1"),
    )
    second = engine.record_fill(
        event_id="fill-event-2",
        fill_id="fill-2",
        occurred_at=T0 + timedelta(seconds=2),
        order_id="order-1",
        quantity=Decimal("6"),
        price=Decimal("110"),
        settlement_currency="USDT",
        settlement_mode=CashSettlementMode.SPOT_NOTIONAL,
        fee_amount=Decimal("1"),
    )
    position = engine.mark_price(
        event_id="mark",
        occurred_at=T0 + timedelta(seconds=3),
        account_id="account-1",
        symbol="BTCUSDT",
        price=Decimal("120"),
    )

    assert first.fill_id == "fill-1"
    assert second.fill_id == "fill-2"
    assert engine.state.orders[0].state is EngineOrderState.FILLED
    assert engine.state.orders[0].filled_quantity == Decimal("10")
    assert position.quantity == Decimal("10")
    assert position.average_price == Decimal("106")
    assert position.unrealized_pnl == Decimal("140")
    assert engine.state.cash[0].balance == Decimal("8938")

    replayed = EventSourcedExecutionEngine.replay(engine.events)
    assert replayed.state.to_json() == engine.state.to_json()
    assert replayed.events_json() == engine.events_json()


def test_derivative_realized_pnl_and_position_flip() -> None:
    engine = EventSourcedExecutionEngine()
    engine.adjust_cash(
        event_id="deposit",
        occurred_at=T0,
        account_id="account-1",
        currency="USDT",
        amount=Decimal("1000"),
        kind=CashFlowKind.DEPOSIT,
    )

    def execute(
        order_id: str,
        side: OrderSide,
        quantity: str,
        price: str,
        second: int,
    ) -> None:
        engine.submit_order(
            event_id=f"{order_id}-submit",
            occurred_at=T0 + timedelta(seconds=second),
            order_id=order_id,
            intent_id=f"intent-{order_id}",
            account_id="account-1",
            venue="perp",
            symbol="BTCUSDT",
            side=side,
            quantity=Decimal(quantity),
            order_type=ExecutionOrderType.MARKET,
        )
        engine.accept_order(
            event_id=f"{order_id}-accept",
            occurred_at=T0 + timedelta(seconds=second),
            order_id=order_id,
        )
        engine.record_fill(
            event_id=f"{order_id}-fill-event",
            fill_id=f"{order_id}-fill",
            occurred_at=T0 + timedelta(seconds=second),
            order_id=order_id,
            quantity=Decimal(quantity),
            price=Decimal(price),
            settlement_currency="USDT",
            settlement_mode=CashSettlementMode.DERIVATIVE_PNL_ONLY,
        )

    execute("short", OrderSide.SELL, "5", "100", 1)
    execute("cover", OrderSide.BUY, "3", "90", 2)
    execute("flip", OrderSide.BUY, "4", "110", 3)
    position = engine.mark_price(
        event_id="mark",
        occurred_at=T0 + timedelta(seconds=4),
        account_id="account-1",
        symbol="BTCUSDT",
        price=Decimal("100"),
    )

    assert position.quantity == Decimal("2")
    assert position.average_price == Decimal("110")
    assert position.realized_pnl == Decimal("10")
    assert position.unrealized_pnl == Decimal("-20")
    assert engine.state.cash[0].balance == Decimal("1010")


def test_multicurrency_cash_and_funding_are_append_only() -> None:
    engine = EventSourcedExecutionEngine()
    usd = engine.adjust_cash(
        event_id="usd-deposit",
        occurred_at=T0,
        account_id="account-1",
        currency="USD",
        amount=Decimal("100"),
        kind=CashFlowKind.DEPOSIT,
    )
    krw = engine.adjust_cash(
        event_id="krw-deposit",
        occurred_at=T0,
        account_id="account-1",
        currency="KRW",
        amount=Decimal("100000"),
        kind=CashFlowKind.DEPOSIT,
    )
    funded = engine.adjust_cash(
        event_id="funding",
        occurred_at=T0 + timedelta(seconds=1),
        account_id="account-1",
        currency="USD",
        amount=Decimal("5"),
        kind=CashFlowKind.FUNDING,
        symbol="BTCUSDT",
    )

    assert usd.balance == Decimal("100")
    assert krw.balance == Decimal("100000")
    assert funded.balance == Decimal("105")
    assert [event.sequence for event in engine.events] == [1, 2, 3]


def test_invalid_transitions_overfills_and_clock_regressions_fail() -> None:
    engine = EventSourcedExecutionEngine()
    _submit(engine)
    with pytest.raises(ValueError, match="accepted"):
        engine.record_fill(
            event_id="bad-fill",
            fill_id="fill",
            occurred_at=T0,
            order_id="order-1",
            quantity=Decimal("1"),
            price=Decimal("100"),
            settlement_currency="USDT",
            settlement_mode=CashSettlementMode.SPOT_NOTIONAL,
        )
    engine.accept_order(event_id="accept", occurred_at=T0, order_id="order-1")
    with pytest.raises(ValueError, match="exceeds"):
        engine.record_fill(
            event_id="overfill",
            fill_id="fill",
            occurred_at=T0,
            order_id="order-1",
            quantity=Decimal("11"),
            price=Decimal("100"),
            settlement_currency="USDT",
            settlement_mode=CashSettlementMode.SPOT_NOTIONAL,
        )
    engine.cancel_order(
        event_id="cancel",
        occurred_at=T0 + timedelta(seconds=2),
        order_id="order-1",
    )
    with pytest.raises(ValueError, match="active"):
        engine.cancel_order(
            event_id="cancel-2",
            occurred_at=T0 + timedelta(seconds=3),
            order_id="order-1",
        )
    with pytest.raises(ValueError, match="non-decreasing"):
        engine.adjust_cash(
            event_id="past",
            occurred_at=T0 + timedelta(seconds=1),
            account_id="account-1",
            currency="USD",
            amount=Decimal("1"),
            kind=CashFlowKind.DEPOSIT,
        )
