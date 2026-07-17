from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.execution_profiles import (
    ExecutionProfileConfidence,
    ExecutionProfileSnapshot,
    ExecutionSourceEvidence,
    InstrumentExecutionRules,
    OrderConstraintCode,
    check_order_constraints,
    fill_notional,
    floor_to_increment,
    funding_ledger_entry,
    require_order_constraints,
    settlement_value_fee_entry,
)
from quant_platform.finance import (
    ExecutionFill,
    ExecutionOrderType,
    ExecutionRealityProfile,
    LedgerEntryKind,
    LiquidityRole,
    OrderSide,
)

D = Decimal
NOW = datetime(2026, 7, 17, tzinfo=UTC)


def _rules() -> InstrumentExecutionRules:
    return InstrumentExecutionRules(
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_tick=D("0.01"),
        quantity_step=D("0.00001"),
        minimum_price=D("0.01"),
        maximum_price=D("1000000"),
        minimum_quantity=D("0.00001"),
        maximum_quantity=D("9000"),
        market_lot_size_overrides=True,
        market_quantity_step=None,
        market_minimum_quantity=D("0"),
        market_maximum_quantity=D("134.37327808"),
        minimum_notional=D("5"),
        maximum_notional=D("9000000"),
        min_notional_applies_to_market=True,
        max_notional_applies_to_market=False,
    )


def _snapshot(*, maker: str = "0.0002", taker: str = "0.0004") -> ExecutionProfileSnapshot:
    profile = ExecutionRealityProfile(
        profile_id="binance-btcusdt-usdm",
        venue="BINANCE",
        market="USDM_PERPETUAL",
        account_type="CROSS_MARGIN",
        settlement_currency="USDT",
        maker_fee_rate=D(maker),
        taker_fee_rate=D(taker),
        minimum_notional=D("5"),
        quantity_step=D("0.00001"),
        price_tick=D("0.01"),
        funding_model="binance-realized-funding-v1",
    )
    source = ExecutionSourceEvidence(
        source_id="exchange-info",
        reference="https://example.test/exchangeInfo",
        observed_at=NOW,
        sha256="a" * 64,
    )
    return ExecutionProfileSnapshot(
        snapshot_id="binance-btcusdt-20260717",
        schema_version="execution-profile-v1",
        profile=profile,
        rules=_rules(),
        observed_at=NOW,
        effective_from=NOW,
        effective_to=None,
        sources=(source,),
        confidence=ExecutionProfileConfidence.CONFIRMED,
    )


def _fill(role: LiquidityRole) -> ExecutionFill:
    return ExecutionFill(
        fill_id="fill-1",
        order_id="order-1",
        venue="BINANCE",
        account_id="paper-main",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=D("0.1"),
        price=D("100000"),
        executed_at=NOW,
        liquidity_role=role,
    )


def test_limit_order_constraints_match_symbol_filters() -> None:
    accepted = check_order_constraints(
        _rules(),
        order_type=ExecutionOrderType.LIMIT,
        quantity=D("0.0001"),
        price=D("100000"),
    )
    assert accepted.accepted is True
    assert accepted.notional == D("10")

    rejected = check_order_constraints(
        _rules(),
        order_type=ExecutionOrderType.LIMIT,
        quantity=D("0.000011"),
        price=D("100000.001"),
    )
    assert OrderConstraintCode.QUANTITY_STEP_MISMATCH in rejected.violations
    assert OrderConstraintCode.PRICE_TICK_MISMATCH in rejected.violations
    assert OrderConstraintCode.NOTIONAL_BELOW_MINIMUM in rejected.violations


def test_market_lot_filter_can_disable_step_size_without_falling_back() -> None:
    result = check_order_constraints(
        _rules(),
        order_type=ExecutionOrderType.MARKET,
        quantity=D("0.000011234"),
        reference_price=D("100000"),
    )
    assert OrderConstraintCode.QUANTITY_STEP_MISMATCH not in result.violations
    assert result.accepted is False
    assert result.violations == (OrderConstraintCode.NOTIONAL_BELOW_MINIMUM,)


def test_market_notional_requires_explicit_reference_price() -> None:
    result = check_order_constraints(
        _rules(),
        order_type=ExecutionOrderType.MARKET,
        quantity=D("0.1"),
    )
    assert result.violations == (OrderConstraintCode.REFERENCE_PRICE_REQUIRED,)
    with pytest.raises(ValueError, match="reference_price_required"):
        require_order_constraints(
            _rules(),
            order_type=ExecutionOrderType.MARKET,
            quantity=D("0.1"),
        )


def test_snapshot_preserves_source_hash_and_profile_rule_identity() -> None:
    snapshot = _snapshot()
    assert snapshot.sources[0].sha256 == "a" * 64
    assert snapshot.rules.symbol == "BTCUSDT"

    mismatched_profile = ExecutionRealityProfile(
        profile_id="bad",
        venue="BINANCE",
        market="USDM_PERPETUAL",
        account_type="CROSS_MARGIN",
        settlement_currency="USDT",
        minimum_notional=D("10"),
        quantity_step=D("0.00001"),
        price_tick=D("0.01"),
    )
    with pytest.raises(ValueError, match="minimum_notional"):
        ExecutionProfileSnapshot(
            snapshot_id="bad",
            schema_version="v1",
            profile=mismatched_profile,
            rules=_rules(),
            observed_at=NOW,
            effective_from=NOW,
            effective_to=None,
            sources=snapshot.sources,
            confidence=ExecutionProfileConfidence.REVIEW_REQUIRED,
        )


def test_settlement_value_fee_entry_handles_commission_and_rebate() -> None:
    taker = settlement_value_fee_entry(
        entry_id="fee-1",
        fill=_fill(LiquidityRole.TAKER),
        snapshot=_snapshot(),
    )
    assert taker.kind is LedgerEntryKind.COMMISSION
    assert taker.amount == D("-4")
    assert taker.currency == "USDT"

    maker_rebate = settlement_value_fee_entry(
        entry_id="rebate-1",
        fill=_fill(LiquidityRole.MAKER),
        snapshot=_snapshot(maker="-0.0001"),
    )
    assert maker_rebate.kind is LedgerEntryKind.REBATE
    assert maker_rebate.amount == D("1")


def test_funding_entry_preserves_signed_account_perspective() -> None:
    received = funding_ledger_entry(
        entry_id="funding-1",
        occurred_at=NOW,
        account_id="paper-main",
        currency="USDT",
        symbol="BTCUSDT",
        amount=D("2.5"),
        related_id="funding-event-1",
    )
    paid = funding_ledger_entry(
        entry_id="funding-2",
        occurred_at=NOW,
        account_id="paper-main",
        currency="USDT",
        symbol="BTCUSDT",
        amount=D("-1.25"),
        related_id="funding-event-2",
    )
    assert received.kind is LedgerEntryKind.FUNDING
    assert received.amount == D("2.5")
    assert paid.amount == D("-1.25")


def test_floor_and_fill_notional_are_decimal_exact() -> None:
    assert floor_to_increment(D("1.239"), D("0.01")) == D("1.23")
    assert fill_notional(_fill(LiquidityRole.MAKER), _rules()) == D("10000")
