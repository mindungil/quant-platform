from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.contracts import MarketBar, Signal
from quant_platform.execution_engine import EventSourcedExecutionEngine
from quant_platform.execution_profiles import (
    ExecutionProfileConfidence,
    ExecutionProfileSnapshot,
    ExecutionSourceEvidence,
    InstrumentExecutionRules,
)
from quant_platform.finance import ExecutionRealityProfile
from quant_platform.paper_contracts import (
    PaperCycleRequest,
    PaperCycleStatus,
    PaperLaunchAuthorization,
)
from quant_platform.paper_orchestrator import PaperTradingOrchestrator
from quant_platform.risk_engine import BrokerSnapshot, SingleStrategyRiskPolicy
from quant_platform.strategy_decision import (
    ApprovalRecord,
    DigestAlgorithm,
    GateResult,
    HoldoutSeal,
    PromotionGate,
    RevisionEvidence,
    RevisionKind,
    StrategyDecisionOutcome,
    StrategyDecisionPackage,
    StrategyLifecycleState,
)
from quant_platform.venue_simulator import VenueOrderStatus, VenueQuote, VenueSimulationConfig

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CRITERIA = "b" * 64


class FixedPlugin:
    name = "paper_strategy"

    def __init__(self, score: float) -> None:
        self.score = score

    def generate(self, bars: tuple[MarketBar, ...]) -> Signal:
        return Signal(
            symbol="BTCUSDT",
            score=self.score,
            generated_at=bars[-1].timestamp,
            source="fixed-paper-test",
        )


def _revisions() -> tuple[RevisionEvidence, ...]:
    return (
        RevisionEvidence(
            "dataset",
            RevisionKind.DATASET,
            "paper dataset",
            "dataset://paper",
            "dataset-v1",
            DigestAlgorithm.SHA256,
            "1" * 64,
        ),
        RevisionEvidence(
            "code",
            RevisionKind.CODE,
            "paper strategy",
            "repo://paper",
            "2" * 40,
            DigestAlgorithm.GIT_SHA1,
            "2" * 40,
        ),
        RevisionEvidence(
            "rule",
            RevisionKind.RULE,
            "paper rules",
            "rule://paper",
            "rule-v1",
            DigestAlgorithm.SHA256,
            "3" * 64,
        ),
    )


def _gate(
    result_id: str,
    gate: PromotionGate,
    when: datetime,
    *,
    criteria: str = "a" * 64,
) -> GateResult:
    return GateResult(
        result_id=result_id,
        gate=gate,
        passed=True,
        evaluated_at=when,
        evaluator="paper-test",
        criteria_sha256=criteria,
        evidence_ids=("dataset", "code", "rule"),
        summary=f"{gate.value} passed",
    )


def _decision(
    decision_id: str,
    source: StrategyLifecycleState,
    target: StrategyLifecycleState,
    gate_id: str,
    when: datetime,
) -> ApprovalRecord:
    return ApprovalRecord(
        decision_id=decision_id,
        from_state=source,
        target_state=target,
        outcome=StrategyDecisionOutcome.APPROVED,
        decided_at=when,
        decided_by="paper-owner",
        gate_result_ids=(gate_id,),
        rationale="pinned evidence passed",
    )


def _paper_package() -> StrategyDecisionPackage:
    package = StrategyDecisionPackage(
        package_id="paper-package-v1",
        strategy_id="paper_strategy",
        package_version="1",
        hypothesis="A fixed signal exercises the paper reference path.",
        state=StrategyLifecycleState.IDEA,
        created_at=T0,
        updated_at=T0,
        revisions=_revisions(),
    )
    steps = (
        (
            "implementation",
            PromotionGate.IMPLEMENTATION,
            StrategyLifecycleState.IDEA,
            StrategyLifecycleState.IMPLEMENTED,
        ),
        (
            "development",
            PromotionGate.DEVELOPMENT_VALIDATION,
            StrategyLifecycleState.IMPLEMENTED,
            StrategyLifecycleState.DEVELOPMENT_VALIDATED,
        ),
    )
    cursor = T0
    for label, gate_kind, source, target in steps:
        cursor += timedelta(minutes=1)
        package = package.add_gate_result(
            _gate(f"gate-{label}", gate_kind, cursor),
            updated_at=cursor,
        )
        cursor += timedelta(minutes=1)
        package = package.apply_decision(
            _decision(f"decision-{label}", source, target, f"gate-{label}", cursor),
            updated_at=cursor,
        )
    cursor += timedelta(minutes=1)
    package = package.seal_holdout(
        HoldoutSeal(
            seal_id="paper-holdout",
            dataset_evidence_id="dataset",
            development_end=date(2024, 12, 31),
            holdout_start=date(2025, 1, 1),
            holdout_end=date(2025, 12, 31),
            split_spec_sha256="4" * 64,
            acceptance_criteria_sha256=CRITERIA,
            sealed_at=cursor,
        ),
        updated_at=cursor,
    )
    cursor += timedelta(minutes=1)
    package = package.add_gate_result(
        _gate("gate-holdout", PromotionGate.HOLDOUT_VALIDATION, cursor, criteria=CRITERIA),
        updated_at=cursor,
    )
    cursor += timedelta(minutes=1)
    package = package.apply_decision(
        _decision(
            "decision-holdout",
            StrategyLifecycleState.DEVELOPMENT_VALIDATED,
            StrategyLifecycleState.HOLDOUT_VALIDATED,
            "gate-holdout",
            cursor,
        ),
        updated_at=cursor,
    )
    cursor += timedelta(minutes=1)
    package = package.add_gate_result(
        _gate("gate-paper", PromotionGate.PAPER_READINESS, cursor),
        updated_at=cursor,
    )
    cursor += timedelta(minutes=1)
    return package.apply_decision(
        _decision(
            "decision-paper",
            StrategyLifecycleState.HOLDOUT_VALIDATED,
            StrategyLifecycleState.PAPER,
            "gate-paper",
            cursor,
        ),
        updated_at=cursor,
    )


def _profile() -> ExecutionProfileSnapshot:
    profile = ExecutionRealityProfile(
        profile_id="paper-profile",
        venue="paper-venue",
        market="perpetual",
        account_type="isolated-margin",
        settlement_currency="USDT",
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0.001"),
        minimum_notional=Decimal("10"),
        quantity_step=Decimal("0.1"),
        price_tick=Decimal("0.1"),
    )
    return ExecutionProfileSnapshot(
        snapshot_id="paper-snapshot",
        schema_version="execution-profile-v1",
        profile=profile,
        rules=InstrumentExecutionRules(
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            price_tick=Decimal("0.1"),
            quantity_step=Decimal("0.1"),
            minimum_quantity=Decimal("0.1"),
            minimum_notional=Decimal("10"),
        ),
        observed_at=T0,
        effective_from=T0,
        effective_to=None,
        sources=(
            ExecutionSourceEvidence(
                source_id="paper-source",
                reference="reference://paper-profile",
                observed_at=T0,
                sha256="5" * 64,
            ),
        ),
        confidence=ExecutionProfileConfidence.CONFIRMED,
    )


def _policy(
    *,
    max_order_notional: Decimal = Decimal("10000"),
    max_data_age: timedelta = timedelta(minutes=5),
) -> SingleStrategyRiskPolicy:
    return SingleStrategyRiskPolicy(
        policy_id="paper-risk",
        schema_version="risk-v1",
        symbol="BTCUSDT",
        settlement_currency="USDT",
        max_order_notional=max_order_notional,
        max_position_notional=Decimal("10000"),
        max_leverage=Decimal("2"),
        max_daily_loss=Decimal("100"),
        max_data_age=max_data_age,
    )


def _authorization(package: StrategyDecisionPackage) -> PaperLaunchAuthorization:
    return PaperLaunchAuthorization.from_package(
        authorization_id="paper-auth",
        session_id="paper-session",
        package=package,
        account_id="account-1",
        symbol="BTCUSDT",
        settlement_currency="USDT",
        venue_profile_snapshot_id="paper-snapshot",
        risk_policy_id="paper-risk",
        authorized_at=package.updated_at + timedelta(minutes=1),
        authorized_by="paper-owner",
        initial_cash=Decimal("1000"),
    )


def _bars(at: datetime) -> tuple[MarketBar, ...]:
    return (
        MarketBar(
            symbol="BTCUSDT",
            timestamp=at - timedelta(hours=1),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000.0,
        ),
    )


def _quote(
    quote_id: str,
    observed_at: datetime,
    *,
    available: str = "10",
) -> VenueQuote:
    return VenueQuote(
        quote_id=quote_id,
        observed_at=observed_at,
        symbol="BTCUSDT",
        bid_price=Decimal("99.5"),
        ask_price=Decimal("100.5"),
        bid_quantity=Decimal(available),
        ask_quantity=Decimal(available),
        trade_price=Decimal("100"),
        trade_volume=Decimal(available),
    )


def _orchestrator(
    *,
    score: float = 0.5,
    policy: SingleStrategyRiskPolicy | None = None,
    venue_config: VenueSimulationConfig | None = None,
) -> PaperTradingOrchestrator:
    package = _paper_package()
    return PaperTradingOrchestrator(
        plugin=FixedPlugin(score),
        decision_package=package,
        authorization=_authorization(package),
        venue_profile=_profile(),
        risk_policy=policy or _policy(),
        venue_config=venue_config,
    )


def _request(
    orchestrator: PaperTradingOrchestrator,
    cycle_id: str = "cycle-1",
    *,
    decision_age: timedelta = timedelta(0),
    available: str = "10",
) -> PaperCycleRequest:
    occurred_at = orchestrator.authorization.authorized_at + timedelta(hours=1)
    return PaperCycleRequest(
        cycle_id=cycle_id,
        occurred_at=occurred_at,
        completed_bars=_bars(occurred_at),
        decision_quote=_quote(
            f"{cycle_id}-decision",
            occurred_at - decision_age,
            available=available,
        ),
        match_quote=_quote(
            f"{cycle_id}-match",
            occurred_at + orchestrator.venue.config.order_latency,
            available=available,
        ),
        daily_pnl=Decimal("0"),
    )


def _state_value(orchestrator: PaperTradingOrchestrator) -> tuple[Decimal, Decimal]:
    position = next(iter(orchestrator.engine.state.positions)).quantity
    cash = next(iter(orchestrator.engine.state.cash)).balance
    return position, cash


def test_paper_launch_requires_an_approved_paper_package() -> None:
    package = _paper_package()
    held = StrategyDecisionPackage(
        package_id=package.package_id,
        strategy_id=package.strategy_id,
        package_version=package.package_version,
        hypothesis=package.hypothesis,
        state=StrategyLifecycleState.HOLDOUT_VALIDATED,
        created_at=package.created_at,
        updated_at=package.updated_at,
        revisions=package.revisions,
        gate_results=package.gate_results,
        decisions=package.decisions[:-1],
        holdout_seal=package.holdout_seal,
    )
    with pytest.raises(ValueError, match="PAPER state"):
        _authorization(held)


def test_full_cycle_replay_reconciliation_and_session_determinism() -> None:
    first = _orchestrator()
    first_result = first.run_cycle(_request(first))

    assert first_result.status is PaperCycleStatus.FILLED
    assert first_result.executed_quantity == Decimal("5.0")
    assert first_result.pre_trade_decision is not None
    assert first_result.pre_trade_decision.decision.allowed is True
    assert first_result.post_trade_decision is not None
    assert first_result.post_trade_decision.decision.allowed is True
    replayed = EventSourcedExecutionEngine.replay(first.engine.events)
    assert replayed.state.to_json() == first.engine.state.to_json()

    position, cash = _state_value(first)
    snapshot = BrokerSnapshot(
        snapshot_id="broker-cycle-1",
        observed_at=first_result.checkpoint.created_at,
        account_id="account-1",
        symbol="BTCUSDT",
        currency="USDT",
        position_quantity=position,
        cash_balance=cash,
        latest_event_sequence=len(first.engine.events),
    )
    reconciled = first.reconcile(
        decision_id="paper-cycle-1-reconcile",
        occurred_at=first_result.checkpoint.created_at,
        broker_snapshot=snapshot,
    )
    assert reconciled.matched is True

    second = _orchestrator()
    second_result = second.run_cycle(_request(second))
    second_position, second_cash = _state_value(second)
    second.reconcile(
        decision_id="paper-cycle-1-reconcile",
        occurred_at=second_result.checkpoint.created_at,
        broker_snapshot=BrokerSnapshot(
            snapshot_id="broker-cycle-1",
            observed_at=second_result.checkpoint.created_at,
            account_id="account-1",
            symbol="BTCUSDT",
            currency="USDT",
            position_quantity=second_position,
            cash_balance=second_cash,
            latest_event_sequence=len(second.engine.events),
        ),
    )
    assert first.session_json() == second.session_json()


def test_risk_reduces_order_before_venue_submission() -> None:
    orchestrator = _orchestrator(policy=_policy(max_order_notional=Decimal("201")))
    result = orchestrator.run_cycle(_request(orchestrator))

    assert result.status is PaperCycleStatus.FILLED
    assert result.requested_order_quantity == Decimal("5")
    assert result.submitted_order_quantity == Decimal("2.0")
    assert result.executed_quantity == Decimal("2.0")
    assert result.pre_trade_decision is not None
    assert result.pre_trade_decision.decision.size_multiplier == pytest.approx(0.4)


def test_partial_fill_cancels_remainder_in_same_cycle() -> None:
    orchestrator = _orchestrator(
        venue_config=VenueSimulationConfig(max_volume_participation=Decimal("0.5"))
    )
    result = orchestrator.run_cycle(_request(orchestrator, available="2"))

    assert result.status is PaperCycleStatus.PARTIALLY_FILLED
    assert result.executed_quantity == Decimal("1.0")
    assert result.venue_order is not None
    assert result.venue_order.status is VenueOrderStatus.CANCELLED
    assert result.event_sequence_end == len(orchestrator.engine.events)


def test_stale_decision_quote_fails_closed_before_order_submission() -> None:
    orchestrator = _orchestrator(policy=_policy(max_data_age=timedelta(seconds=5)))
    result = orchestrator.run_cycle(
        _request(orchestrator, decision_age=timedelta(minutes=1))
    )

    assert result.status is PaperCycleStatus.RISK_REJECTED
    assert result.venue_order is None
    assert len(orchestrator.engine.state.orders) == 0
    assert len(orchestrator.engine.events) == 1
