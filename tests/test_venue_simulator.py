from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.execution_engine import EventSourcedExecutionEngine
from quant_platform.execution_profiles import (
    ExecutionProfileConfidence,
    ExecutionProfileSnapshot,
    ExecutionSourceEvidence,
    InstrumentExecutionRules,
)
from quant_platform.finance import (
    ExecutionOrderType,
    ExecutionRealityProfile,
    OrderSide,
)
from quant_platform.venue_simulator import (
    DeterministicVenueSimulator,
    VenueOrderRequest,
    VenueOrderStatus,
    VenueQuote,
    VenueSimulationConfig,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _snapshot(
    maker: Decimal = Decimal("-0.0001"),
    taker: Decimal = Decimal("0.001"),
) -> ExecutionProfileSnapshot:
    profile = ExecutionRealityProfile(
        profile_id="profile-v1",
        venue="reference-venue",
        market="spot",
        account_type="cash",
        settlement_currency="USDT",
        maker_fee_rate=maker,
        taker_fee_rate=taker,
        minimum_notional=Decimal("10"),
        quantity_step=Decimal("0.1"),
        price_tick=Decimal("0.5"),
    )
    rules = InstrumentExecutionRules(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_tick=Decimal("0.5"),
        quantity_step=Decimal("0.1"),
        minimum_quantity=Decimal("0.1"),
        minimum_notional=Decimal("10"),
    )
    return ExecutionProfileSnapshot(
        snapshot_id="snapshot-v1",
        schema_version="execution-profile-v1",
        profile=profile,
        rules=rules,
        observed_at=T0,
        effective_from=T0,
        effective_to=None,
        sources=(
            ExecutionSourceEvidence(
                source_id="source-v1",
                reference="reference://execution-profile",
                observed_at=T0,
                sha256="a" * 64,
            ),
        ),
        confidence=ExecutionProfileConfidence.CONFIRMED,
    )


def _quote(
    observed_at: datetime = T0,
    *,
    trade_price: Decimal = Decimal("100"),
    trade_volume: Decimal = Decimal("10"),
) -> VenueQuote:
    return VenueQuote(
        quote_id=f"quote-{int(observed_at.timestamp())}",
        observed_at=observed_at,
        symbol="BTCUSDT",
        bid_price=Decimal("99.5"),
        ask_price=Decimal("100.5"),
        bid_quantity=Decimal("3"),
        ask_quantity=Decimal("2"),
        trade_price=trade_price,
        trade_volume=trade_volume,
    )


def _request(
    order_id: str = "order-1",
    quantity: str = "3",
    order_type: ExecutionOrderType = ExecutionOrderType.MARKET,
    limit_price: str | None = None,
    submitted_at: datetime = T0,
    replaces_order_id: str | None = None,
) -> VenueOrderRequest:
    return VenueOrderRequest(
        order_id=order_id,
        intent_id=f"intent-{order_id}",
        account_id="account-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal(quantity),
        order_type=order_type,
        submitted_at=submitted_at,
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        replaces_order_id=replaces_order_id,
    )


def test_market_partial_fill_latency_fee_and_replay() -> None:
    simulator = DeterministicVenueSimulator(
        _snapshot(),
        VenueSimulationConfig(
            order_latency=timedelta(seconds=2),
            max_volume_participation=Decimal("0.5"),
        ),
    )
    record = simulator.submit(_request(), reference_quote=_quote())

    assert record.accepted_at == T0 + timedelta(seconds=2)
    with pytest.raises(ValueError, match="precedes"):
        simulator.match("order-1", _quote(T0 + timedelta(seconds=1)))

    first = simulator.match("order-1", _quote(T0 + timedelta(seconds=2)))
    assert first.order.status is VenueOrderStatus.PARTIALLY_FILLED
    assert first.fill is not None
    assert first.fill.quantity == Decimal("1.0")
    assert first.fill.liquidity_role.value == "TAKER"
    assert first.fill.profile_snapshot_id == "snapshot-v1"
    assert first.fill.model_version == "volume-participation-v1"
    assert first.fill.fee_amount == Decimal("0.10050")

    second = simulator.match("order-1", _quote(T0 + timedelta(seconds=3)))
    assert second.remaining_quantity == Decimal("1.0")

    replayed = EventSourcedExecutionEngine.replay(simulator.engine.events)
    assert replayed.state.to_json() == simulator.engine.state.to_json()


def test_passive_limit_is_maker_and_rebate_is_cash_event() -> None:
    simulator = DeterministicVenueSimulator(_snapshot())
    simulator.submit(
        _request(
            order_type=ExecutionOrderType.LIMIT,
            limit_price="99.5",
            quantity="1",
        ),
        reference_quote=_quote(),
    )

    result = simulator.match(
        "order-1",
        _quote(trade_price=Decimal("99"), trade_volume=Decimal("1")),
    )

    assert result.fill is not None
    assert result.fill.liquidity_role.value == "MAKER"
    assert result.fill.signed_fee_cashflow > 0
    assert simulator.engine.state.cash[0].balance > 0


def test_constraints_reject_tick_lot_and_notional() -> None:
    simulator = DeterministicVenueSimulator(_snapshot())
    record = simulator.submit(
        _request(
            quantity="0.15",
            order_type=ExecutionOrderType.LIMIT,
            limit_price="99.7",
        ),
        reference_quote=_quote(),
    )

    assert record.status is VenueOrderStatus.REJECTED
    assert {violation.value for violation in record.violations} == {
        "quantity_step_mismatch",
        "price_tick_mismatch",
    }


def test_cancel_replace_and_non_crossing_limit() -> None:
    simulator = DeterministicVenueSimulator(_snapshot())
    simulator.submit(
        _request(
            order_type=ExecutionOrderType.LIMIT,
            limit_price="90",
            quantity="1",
        ),
        reference_quote=_quote(),
    )

    no_fill = simulator.match("order-1", _quote(trade_price=Decimal("100")))
    assert no_fill.fill is None

    replacement = _request(
        order_id="order-2",
        quantity="1",
        order_type=ExecutionOrderType.LIMIT,
        limit_price="100.5",
        submitted_at=T0 + timedelta(seconds=1),
        replaces_order_id="order-1",
    )
    record = simulator.replace(
        "order-1",
        replacement,
        occurred_at=T0 + timedelta(seconds=1),
        reference_quote=_quote(T0 + timedelta(seconds=1)),
    )

    assert record.status is VenueOrderStatus.ACCEPTED
    assert simulator.orders[0].status is VenueOrderStatus.CANCELLED
