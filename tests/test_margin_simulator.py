from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.execution_engine import (
    CashFlowKind,
    CashSettlementMode,
    EventSourcedExecutionEngine,
)
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
from quant_platform.margin_simulator import (
    FinancingChargeKind,
    IsolatedMarginProfile,
    IsolatedMarginSimulator,
    PeriodicFundingSchedule,
    VersionedVenueProfile,
)
from quant_platform.venue_simulator import DeterministicVenueSimulator

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _execution_snapshot(*, multiplier: Decimal = Decimal("1")) -> ExecutionProfileSnapshot:
    profile = ExecutionRealityProfile(
        profile_id="perpetual-profile-v1",
        venue="reference-venue",
        market="perpetual",
        account_type="isolated-margin",
        settlement_currency="USDT",
        maker_fee_rate=Decimal("-0.0001"),
        taker_fee_rate=Decimal("0.001"),
        minimum_notional=Decimal("10"),
        quantity_step=Decimal("0.1"),
        price_tick=Decimal("0.5"),
        contract_multiplier=multiplier,
        funding_model="periodic-funding-v1",
        borrow_model="simple-annual-v1",
        margin_model="isolated-linear-margin-v1",
    )
    rules = InstrumentExecutionRules(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_tick=Decimal("0.5"),
        quantity_step=Decimal("0.1"),
        minimum_quantity=Decimal("0.1"),
        minimum_notional=Decimal("10"),
        contract_multiplier=multiplier,
    )
    return ExecutionProfileSnapshot(
        snapshot_id="execution-snapshot-v1",
        schema_version="execution-profile-v1",
        profile=profile,
        rules=rules,
        observed_at=T0,
        effective_from=T0,
        effective_to=None,
        sources=(
            ExecutionSourceEvidence(
                source_id="source-v1",
                reference="reference://perpetual-profile",
                observed_at=T0,
                sha256="a" * 64,
            ),
        ),
        confidence=ExecutionProfileConfidence.CONFIRMED,
    )


def _venue_profile() -> VersionedVenueProfile:
    execution = _execution_snapshot()
    margin = IsolatedMarginProfile(
        profile_id="isolated-margin-v1",
        schema_version="isolated-margin-profile-v1",
        execution_snapshot_id=execution.snapshot_id,
        settlement_currency="USDT",
        initial_margin_rate=Decimal("0.10"),
        maintenance_margin_rate=Decimal("0.05"),
        liquidation_fee_rate=Decimal("0.01"),
        funding_schedule=PeriodicFundingSchedule(
            schedule_id="eight-hour-funding-v1",
            anchor_at=T0,
            interval=timedelta(hours=8),
        ),
        borrow_rate_per_year=Decimal("0.365"),
        margin_interest_rate_per_year=Decimal("0.73"),
    )
    return VersionedVenueProfile(execution=execution, margin=margin)


def _open_position(
    engine: EventSourcedExecutionEngine,
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: Decimal = Decimal("2"),
    price: Decimal = Decimal("100"),
    deposit: Decimal = Decimal("1000"),
) -> None:
    engine.adjust_cash(
        event_id="deposit",
        occurred_at=T0,
        account_id="account-1",
        currency="USDT",
        amount=deposit,
        kind=CashFlowKind.DEPOSIT,
    )
    engine.submit_order(
        event_id="open-submit",
        occurred_at=T0,
        order_id="open-order",
        intent_id="open-intent",
        account_id="account-1",
        venue="reference-venue",
        symbol="BTCUSDT",
        side=side,
        quantity=quantity,
        order_type=ExecutionOrderType.MARKET,
    )
    engine.accept_order(
        event_id="open-accept",
        occurred_at=T0,
        order_id="open-order",
    )
    engine.record_fill(
        event_id="open-fill-event",
        fill_id="open-fill",
        occurred_at=T0,
        order_id="open-order",
        quantity=quantity,
        price=price,
        settlement_currency="USDT",
        settlement_mode=CashSettlementMode.DERIVATIVE_PNL_ONLY,
    )


def test_long_pays_and_short_receives_positive_funding() -> None:
    long_engine = EventSourcedExecutionEngine()
    _open_position(long_engine)
    long_simulator = IsolatedMarginSimulator(_venue_profile(), long_engine)
    long_evidence = long_simulator.apply_funding(
        event_id="long-funding",
        account_id="account-1",
        occurred_at=T0 + timedelta(hours=8),
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )

    short_engine = EventSourcedExecutionEngine()
    _open_position(short_engine, side=OrderSide.SELL)
    short_simulator = IsolatedMarginSimulator(_venue_profile(), short_engine)
    short_evidence = short_simulator.apply_funding(
        event_id="short-funding",
        account_id="account-1",
        occurred_at=T0 + timedelta(hours=8),
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )

    assert long_evidence.position_notional == Decimal("200")
    assert long_evidence.cash_amount == Decimal("-0.200")
    assert short_evidence.cash_amount == Decimal("0.200")
    assert long_engine.state.cash[0].balance == Decimal("999.800")
    assert short_engine.state.cash[0].balance == Decimal("1000.200")


def test_funding_schedule_and_duplicate_application_fail_closed() -> None:
    engine = EventSourcedExecutionEngine()
    _open_position(engine)
    simulator = IsolatedMarginSimulator(_venue_profile(), engine)

    with pytest.raises(ValueError, match="aligned"):
        simulator.apply_funding(
            event_id="early-funding",
            account_id="account-1",
            occurred_at=T0 + timedelta(hours=1),
            funding_rate=Decimal("0.001"),
            mark_price=Decimal("100"),
        )

    simulator.apply_funding(
        event_id="scheduled-funding",
        account_id="account-1",
        occurred_at=T0 + timedelta(hours=8),
        funding_rate=Decimal("0.001"),
        mark_price=Decimal("100"),
    )
    with pytest.raises(ValueError, match="already applied"):
        simulator.apply_funding(
            event_id="duplicate-funding",
            account_id="account-1",
            occurred_at=T0 + timedelta(hours=8),
            funding_rate=Decimal("0.001"),
            mark_price=Decimal("100"),
        )


def test_borrow_and_margin_interest_use_explicit_principal_and_elapsed_time() -> None:
    engine = EventSourcedExecutionEngine()
    _open_position(engine)
    simulator = IsolatedMarginSimulator(_venue_profile(), engine)

    borrow = simulator.accrue_borrow_interest(
        event_id="borrow-interest",
        account_id="account-1",
        occurred_at=T0 + timedelta(days=10),
        principal=Decimal("1000"),
        elapsed=timedelta(days=10),
    )
    margin = simulator.accrue_margin_interest(
        event_id="margin-interest",
        account_id="account-1",
        occurred_at=T0 + timedelta(days=15),
        principal=Decimal("500"),
        elapsed=timedelta(days=5),
    )

    assert borrow.kind is FinancingChargeKind.BORROW_INTEREST
    assert borrow.cash_amount == Decimal("-10.000")
    assert margin.kind is FinancingChargeKind.MARGIN_INTEREST
    assert margin.cash_amount == Decimal("-5.00")
    assert engine.state.cash[0].balance == Decimal("985.000")


def test_hand_calculated_liquidation_closes_position_and_replays() -> None:
    engine = EventSourcedExecutionEngine()
    _open_position(
        engine,
        quantity=Decimal("1"),
        price=Decimal("100"),
        deposit=Decimal("10"),
    )
    simulator = IsolatedMarginSimulator(_venue_profile(), engine)

    before = simulator.evaluate(
        account_id="account-1",
        observed_at=T0 + timedelta(hours=1),
        mark_price=Decimal("90"),
    )
    result = simulator.liquidate(
        liquidation_id="liquidation-1",
        account_id="account-1",
        occurred_at=T0 + timedelta(hours=1),
        trigger_mark_price=Decimal("90"),
        execution_price=Decimal("89"),
    )

    assert before.equity == Decimal("0")
    assert before.maintenance_margin_requirement == Decimal("4.50")
    assert before.liquidation_fee_reserve == Decimal("0.90")
    assert before.liquidatable is True
    assert result.liquidation_fee == Decimal("0.89")
    assert result.after.position_quantity == Decimal("0")
    assert result.after.cash_balance == Decimal("-1.89")

    replayed = EventSourcedExecutionEngine.replay(engine.events)
    replayed_after = IsolatedMarginSimulator(_venue_profile(), replayed).evaluate(
        account_id="account-1",
        observed_at=T0 + timedelta(hours=1),
        mark_price=Decimal("89"),
    )
    assert replayed.events_json() == engine.events_json()
    assert replayed.state.to_json() == engine.state.to_json()
    assert replayed_after.to_json() == result.after.to_json()


def test_healthy_account_is_not_mutated_by_liquidation_attempt() -> None:
    engine = EventSourcedExecutionEngine()
    _open_position(engine, quantity=Decimal("1"), deposit=Decimal("100"))
    simulator = IsolatedMarginSimulator(_venue_profile(), engine)
    events_before = engine.events_json()

    with pytest.raises(ValueError, match="does not meet"):
        simulator.liquidate(
            liquidation_id="liquidation-healthy",
            account_id="account-1",
            occurred_at=T0 + timedelta(hours=1),
            trigger_mark_price=Decimal("90"),
            execution_price=Decimal("89"),
        )

    assert engine.events_json() == events_before


def test_one_versioned_profile_is_reused_by_matching_and_margin_layers() -> None:
    profile = _venue_profile()
    independent = _venue_profile()
    matching = DeterministicVenueSimulator(profile)
    margin = IsolatedMarginSimulator(profile, matching.engine)

    assert matching.snapshot is profile
    assert matching.snapshot.execution is profile.execution
    assert margin.profile is profile
    assert profile.profile_key == "execution-snapshot-v1:isolated-margin-v1"
    assert profile.to_json() == independent.to_json()


def test_profile_rejects_mismatch_and_non_unit_contracts() -> None:
    profile = _venue_profile()
    mismatched = IsolatedMarginProfile(
        profile_id="bad-margin",
        schema_version="isolated-margin-profile-v1",
        execution_snapshot_id="other-execution",
        settlement_currency="USDT",
        initial_margin_rate=Decimal("0.10"),
        maintenance_margin_rate=Decimal("0.05"),
        liquidation_fee_rate=Decimal("0.01"),
        funding_schedule=profile.margin.funding_schedule,
    )
    with pytest.raises(ValueError, match="reference"):
        VersionedVenueProfile(profile.execution, mismatched)

    non_unit = _execution_snapshot(multiplier=Decimal("0.001"))
    matching_margin = IsolatedMarginProfile(
        profile_id="non-unit-margin",
        schema_version="isolated-margin-profile-v1",
        execution_snapshot_id=non_unit.snapshot_id,
        settlement_currency="USDT",
        initial_margin_rate=Decimal("0.10"),
        maintenance_margin_rate=Decimal("0.05"),
        liquidation_fee_rate=Decimal("0.01"),
        funding_schedule=profile.margin.funding_schedule,
    )
    with pytest.raises(ValueError, match="unit contract multiplier"):
        VersionedVenueProfile(non_unit, matching_margin)
