from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quant_platform.execution_engine import (
    CashFlowKind,
    CashSettlementMode,
    EventSourcedExecutionEngine,
)
from quant_platform.finance import ExecutionOrderType, OrderSide
from quant_platform.margin_simulator import MarginAccountSnapshot
from quant_platform.risk_engine import (
    BrokerSnapshot,
    KillSwitchSource,
    PreTradeRiskRequest,
    RiskCheckpoint,
    RiskViolationCode,
    SingleStrategyRiskEngine,
    SingleStrategyRiskPolicy,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _policy(
    *,
    max_order_notional: str = "100",
    max_position_notional: str = "200",
    max_leverage: str = "2",
    max_daily_loss: str = "50",
) -> SingleStrategyRiskPolicy:
    return SingleStrategyRiskPolicy(
        policy_id="single-strategy-risk-v1",
        schema_version="risk-policy-v1",
        symbol="BTCUSDT",
        settlement_currency="USDT",
        max_order_notional=Decimal(max_order_notional),
        max_position_notional=Decimal(max_position_notional),
        max_leverage=Decimal(max_leverage),
        max_daily_loss=Decimal(max_daily_loss),
        max_data_age=timedelta(minutes=1),
    )


def _request(
    decision_id: str,
    *,
    occurred_at: datetime,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "0.5",
    current_position_quantity: str = "0",
    equity: str = "100",
    daily_pnl: str = "0",
    reduce_only: bool = False,
    data_age: timedelta = timedelta(seconds=10),
) -> PreTradeRiskRequest:
    return PreTradeRiskRequest(
        decision_id=decision_id,
        occurred_at=occurred_at,
        account_id="account-1",
        symbol="BTCUSDT",
        side=side,
        quantity=Decimal(quantity),
        reference_price=Decimal("100"),
        data_observed_at=occurred_at - data_age,
        current_position_quantity=Decimal(current_position_quantity),
        equity=Decimal(equity),
        daily_pnl=Decimal(daily_pnl),
        reduce_only=reduce_only,
    )


def _open_position() -> EventSourcedExecutionEngine:
    engine = EventSourcedExecutionEngine()
    engine.adjust_cash(
        event_id="deposit",
        occurred_at=T0,
        account_id="account-1",
        currency="USDT",
        amount=Decimal("1000"),
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
        side=OrderSide.BUY,
        quantity=Decimal("1"),
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
        quantity=Decimal("1"),
        price=Decimal("100"),
        settlement_currency="USDT",
        settlement_mode=CashSettlementMode.DERIVATIVE_PNL_ONLY,
    )
    return engine


def test_pre_trade_allows_normal_order_and_reduces_oversized_order() -> None:
    risk = SingleStrategyRiskEngine(_policy())

    allowed = risk.pre_trade(_request("allow", occurred_at=T0))
    reduced = risk.pre_trade(
        _request(
            "reduce-size",
            occurred_at=T0 + timedelta(seconds=1),
            quantity="2",
        )
    )

    assert allowed.decision.allowed is True
    assert allowed.decision.size_multiplier == 1.0
    assert reduced.decision.allowed is True
    assert reduced.decision.size_multiplier == 0.5
    assert {item.code for item in reduced.violations} == {
        RiskViolationCode.ORDER_NOTIONAL_LIMIT,
    }
    assert any(item.name == "allowed_quantity" and item.value == "1" for item in reduced.metrics)


def test_daily_loss_and_stale_data_fail_closed() -> None:
    risk = SingleStrategyRiskEngine(_policy())

    daily_loss = risk.pre_trade(
        _request(
            "daily-loss",
            occurred_at=T0,
            daily_pnl="-50",
        )
    )
    stale = risk.pre_trade(
        _request(
            "stale",
            occurred_at=T0 + timedelta(seconds=1),
            data_age=timedelta(minutes=2),
        )
    )

    assert daily_loss.decision.allowed is False
    assert daily_loss.decision.size_multiplier == 0.0
    assert RiskViolationCode.DAILY_LOSS_LIMIT in {
        item.code for item in daily_loss.violations
    }
    assert stale.decision.allowed is False
    assert RiskViolationCode.STALE_DATA in {item.code for item in stale.violations}


def test_kill_switch_blocks_new_risk_but_allows_valid_reduce_only_exit() -> None:
    risk = SingleStrategyRiskEngine(_policy())
    event = risk.engage_kill_switch(
        event_id="manual-kill",
        occurred_at=T0,
        reason="operator requested stop",
    )

    blocked = risk.pre_trade(
        _request(
            "blocked-new",
            occurred_at=T0 + timedelta(seconds=1),
            current_position_quantity="1",
        )
    )
    exit_order = risk.pre_trade(
        _request(
            "reduce-exposure",
            occurred_at=T0 + timedelta(seconds=2),
            side=OrderSide.SELL,
            quantity="0.5",
            current_position_quantity="1",
            reduce_only=True,
        )
    )
    reversal = risk.pre_trade(
        _request(
            "invalid-reversal",
            occurred_at=T0 + timedelta(seconds=3),
            side=OrderSide.SELL,
            quantity="2",
            current_position_quantity="1",
            reduce_only=True,
        )
    )

    assert event.source is KillSwitchSource.MANUAL
    assert blocked.decision.allowed is False
    assert RiskViolationCode.KILL_SWITCH_ENGAGED in {
        item.code for item in blocked.violations
    }
    assert exit_order.decision.allowed is True
    assert reversal.decision.allowed is False
    assert RiskViolationCode.REDUCE_ONLY_VIOLATION in {
        item.code for item in reversal.violations
    }


def test_post_trade_checkpoint_and_replay_health() -> None:
    engine = _open_position()
    risk = SingleStrategyRiskEngine(_policy())

    post_trade = risk.assess_post_trade(
        decision_id="post-trade",
        occurred_at=T0 + timedelta(seconds=1),
        account_id="account-1",
        engine=engine,
    )
    checkpoint = RiskCheckpoint.from_engine(
        checkpoint_id="checkpoint-1",
        created_at=T0 + timedelta(seconds=1),
        engine=engine,
    )
    healthy = risk.inspect_stream(
        decision_id="health-ok",
        occurred_at=T0 + timedelta(seconds=2),
        account_id="account-1",
        latest_market_data_at=T0 + timedelta(seconds=2),
        engine=engine,
        checkpoint=checkpoint,
    )

    engine.mark_price(
        event_id="mark-after-checkpoint",
        occurred_at=T0 + timedelta(seconds=3),
        account_id="account-1",
        symbol="BTCUSDT",
        price=Decimal("99"),
    )
    diverged = risk.inspect_stream(
        decision_id="health-diverged",
        occurred_at=T0 + timedelta(seconds=3),
        account_id="account-1",
        latest_market_data_at=T0 + timedelta(seconds=3),
        engine=engine,
        checkpoint=checkpoint,
    )

    assert post_trade.decision.allowed is True
    assert healthy.decision.allowed is True
    assert diverged.decision.allowed is False
    assert risk.kill_switch_engaged is True
    assert RiskViolationCode.CHECKPOINT_SEQUENCE_MISMATCH in {
        item.code for item in diverged.violations
    }
    assert risk.kill_switch_events[-1].source is KillSwitchSource.AUTOMATIC


def test_reconciliation_detects_position_cash_and_sequence_mismatch() -> None:
    engine = _open_position()
    risk = SingleStrategyRiskEngine(_policy())

    matched = risk.reconcile(
        decision_id="reconcile-ok",
        occurred_at=T0 + timedelta(seconds=1),
        engine=engine,
        broker=BrokerSnapshot(
            snapshot_id="broker-ok",
            observed_at=T0 + timedelta(seconds=1),
            account_id="account-1",
            symbol="BTCUSDT",
            currency="USDT",
            position_quantity=Decimal("1"),
            cash_balance=Decimal("1000"),
            latest_event_sequence=len(engine.events),
        ),
    )
    mismatch = risk.reconcile(
        decision_id="reconcile-bad",
        occurred_at=T0 + timedelta(seconds=2),
        engine=engine,
        broker=BrokerSnapshot(
            snapshot_id="broker-bad",
            observed_at=T0 + timedelta(seconds=2),
            account_id="account-1",
            symbol="BTCUSDT",
            currency="USDT",
            position_quantity=Decimal("0.8"),
            cash_balance=Decimal("999"),
            latest_event_sequence=len(engine.events) - 1,
        ),
    )

    assert matched.matched is True
    assert mismatch.matched is False
    assert risk.kill_switch_engaged is True
    assert {item.code for item in mismatch.decision.violations} == {
        RiskViolationCode.BROKER_POSITION_MISMATCH,
        RiskViolationCode.BROKER_CASH_MISMATCH,
        RiskViolationCode.BROKER_SEQUENCE_MISMATCH,
    }


def test_margin_threshold_automatically_engages_kill_switch() -> None:
    engine = _open_position()
    risk = SingleStrategyRiskEngine(_policy())
    snapshot = MarginAccountSnapshot(
        account_id="account-1",
        symbol="BTCUSDT",
        currency="USDT",
        observed_at=T0 + timedelta(seconds=1),
        cash_balance=Decimal("0"),
        position_quantity=Decimal("1"),
        average_price=Decimal("100"),
        mark_price=Decimal("90"),
        position_notional=Decimal("90"),
        unrealized_pnl=Decimal("-10"),
        equity=Decimal("-10"),
        initial_margin_requirement=Decimal("9"),
        maintenance_margin_requirement=Decimal("4.5"),
        liquidation_fee_reserve=Decimal("0.9"),
        available_equity=Decimal("-19"),
        margin_excess=Decimal("-15.4"),
        liquidatable=True,
        profile_key="execution-v1:margin-v1",
    )

    decision = risk.assess_post_trade(
        decision_id="margin-danger",
        occurred_at=T0 + timedelta(seconds=1),
        account_id="account-1",
        engine=engine,
        margin_snapshot=snapshot,
    )

    assert decision.decision.allowed is False
    assert RiskViolationCode.MARGIN_INVARIANT in {
        item.code for item in decision.violations
    }
    assert risk.kill_switch_engaged is True


def test_identical_workflows_produce_byte_identical_audit_logs() -> None:
    def execute() -> str:
        engine = _open_position()
        risk = SingleStrategyRiskEngine(_policy())
        risk.pre_trade(_request("allow", occurred_at=T0 + timedelta(seconds=1)))
        risk.assess_post_trade(
            decision_id="post-trade",
            occurred_at=T0 + timedelta(seconds=2),
            account_id="account-1",
            engine=engine,
        )
        checkpoint = RiskCheckpoint.from_engine(
            checkpoint_id="checkpoint",
            created_at=T0 + timedelta(seconds=2),
            engine=engine,
        )
        risk.inspect_stream(
            decision_id="health",
            occurred_at=T0 + timedelta(seconds=3),
            account_id="account-1",
            latest_market_data_at=T0 + timedelta(seconds=3),
            engine=engine,
            checkpoint=checkpoint,
        )
        return risk.audit_json()

    first = execute()
    second = execute()

    assert first == second
    assert first.startswith("[")
