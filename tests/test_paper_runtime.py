from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from quant_platform.contracts import MarketBar, Signal
from quant_platform.execution_profiles import (
    ExecutionProfileConfidence,
    ExecutionProfileSnapshot,
    ExecutionSourceEvidence,
    InstrumentExecutionRules,
)
from quant_platform.finance import ExecutionRealityProfile
from quant_platform.paper_contracts import (
    PaperCycleRequest,
    PaperCycleResult,
    PaperCycleStatus,
    PaperLaunchAuthorization,
)
from quant_platform.paper_runtime import (
    DurablePaperRuntime,
    PaperIdempotencyConflictError,
    PaperLeaseConflictError,
    PaperRecoveryIntegrityError,
    PaperRuntimeError,
)
from quant_platform.paper_runtime_contracts import PaperOperationStatus
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
from quant_platform.venue_simulator import VenueQuote, VenueSimulationConfig

T0 = datetime(2026, 1, 1, tzinfo=UTC)
CRITERIA = "b" * 64


class FixedPlugin:
    name = "paper_strategy"

    def __init__(self, score: float = 0.5) -> None:
        self.score = score

    def generate(self, bars: tuple[MarketBar, ...]) -> Signal:
        return Signal(
            symbol="BTCUSDT",
            score=self.score,
            generated_at=bars[-1].timestamp,
            source="durable-paper-test",
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
        hypothesis="A fixed signal exercises the durable paper path.",
        state=StrategyLifecycleState.IDEA,
        created_at=T0,
        updated_at=T0,
        revisions=_revisions(),
    )
    cursor = T0
    transitions = (
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
    for label, gate_kind, source, target in transitions:
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
        _gate(
            "gate-holdout",
            PromotionGate.HOLDOUT_VALIDATION,
            cursor,
            criteria=CRITERIA,
        ),
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


def _policy(*, max_order_notional: Decimal = Decimal("10000")) -> SingleStrategyRiskPolicy:
    return SingleStrategyRiskPolicy(
        policy_id="paper-risk",
        schema_version="risk-v1",
        symbol="BTCUSDT",
        settlement_currency="USDT",
        max_order_notional=max_order_notional,
        max_position_notional=Decimal("10000"),
        max_leverage=Decimal("2"),
        max_daily_loss=Decimal("100"),
        max_data_age=timedelta(minutes=5),
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


def _runtime(
    path: Path,
    *,
    policy: SingleStrategyRiskPolicy | None = None,
) -> DurablePaperRuntime:
    package = _paper_package()
    return DurablePaperRuntime(
        path,
        plugin=FixedPlugin(),
        decision_package=package,
        authorization=_authorization(package),
        venue_profile=_profile(),
        risk_policy=policy or _policy(),
        venue_config=VenueSimulationConfig(),
    )


def _quote(quote_id: str, observed_at: datetime, *, available: str = "10") -> VenueQuote:
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


def _request(runtime: DurablePaperRuntime, cycle_id: str = "cycle-1") -> PaperCycleRequest:
    occurred_at = runtime.authorization.authorized_at + timedelta(hours=1)
    return PaperCycleRequest(
        cycle_id=cycle_id,
        occurred_at=occurred_at,
        completed_bars=(
            MarketBar(
                symbol="BTCUSDT",
                timestamp=occurred_at - timedelta(hours=1),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
            ),
        ),
        decision_quote=_quote(f"{cycle_id}-decision", occurred_at),
        match_quote=_quote(f"{cycle_id}-match", occurred_at),
        daily_pnl=Decimal("0"),
    )


def _broker(runtime: DurablePaperRuntime, observed_at: datetime) -> BrokerSnapshot:
    state = runtime.orchestrator.engine.state
    return BrokerSnapshot(
        snapshot_id="broker-1",
        observed_at=observed_at,
        account_id="account-1",
        symbol="BTCUSDT",
        currency="USDT",
        position_quantity=state.positions[0].quantity,
        cash_balance=state.cash[0].balance,
        latest_event_sequence=len(runtime.orchestrator.engine.events),
    )


def test_restart_idempotency_reconciliation_and_daily_report(tmp_path: Path) -> None:
    database = tmp_path / "paper.db"
    runtime = _runtime(database)
    request = _request(runtime)
    runtime.acquire_lease(
        owner_id="worker-a",
        now=request.occurred_at - timedelta(minutes=2),
        ttl=timedelta(minutes=1),
    )
    lease = runtime.acquire_lease(
        owner_id="worker-a",
        now=request.occurred_at,
        ttl=timedelta(minutes=10),
    )
    result = runtime.run_cycle(request, lease=lease, now=request.occurred_at)
    assert result.status is PaperCycleStatus.FILLED

    reconcile_at = request.occurred_at + timedelta(minutes=1)
    reconciliation = runtime.reconcile(
        operation_id="reconcile-1",
        decision_id="risk-reconcile-1",
        occurred_at=reconcile_at,
        broker_snapshot=_broker(runtime, reconcile_at),
        lease=lease,
        now=reconcile_at,
    )
    assert reconciliation.matched is True
    report = runtime.record_daily_report(
        request.occurred_at.date(),
        lease=lease,
        now=reconcile_at + timedelta(minutes=1),
    )
    assert report.healthy is True
    assert report.cycle_count == 1
    assert report.reconciliation_count == 1
    assert report.reconciliation_mismatch_count == 0
    assert report.executed_quantity == Decimal("5.0")
    assert report.fee_amount == Decimal("0.50250")
    assert "Healthy: **YES**" in report.to_markdown()
    session_json = runtime.orchestrator.session_json()
    operation_count = len(runtime.operations)
    runtime.close()

    restored = _runtime(database)
    assert restored.verify_integrity().operation_sequence == 2
    assert restored.orchestrator.session_json() == session_json
    takeover = restored.acquire_lease(
        owner_id="worker-b",
        now=reconcile_at + timedelta(minutes=20),
        ttl=timedelta(minutes=10),
    )
    duplicate = restored.run_cycle(request, lease=takeover, now=takeover.acquired_at)
    assert duplicate.to_json() == result.to_json()
    assert len(restored.operations) == operation_count
    assert restored.build_daily_report(request.occurred_at.date()).to_json() == report.to_json()
    restored.close()


def test_staged_cycle_is_recovered_after_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "recover.db"
    runtime = _runtime(database)
    request = _request(runtime)
    lease = runtime.acquire_lease(
        owner_id="worker-a",
        now=request.occurred_at,
        ttl=timedelta(minutes=1),
    )
    staged = runtime.stage_cycle(request, lease=lease, now=request.occurred_at)
    assert staged.status is PaperOperationStatus.PENDING
    runtime.close()

    restored = _runtime(database)
    assert restored.pending_operations[0].operation_id == request.cycle_id
    takeover = restored.acquire_lease(
        owner_id="worker-b",
        now=request.occurred_at + timedelta(minutes=2),
        ttl=timedelta(minutes=5),
    )
    recovered = restored.recover_pending(
        lease=takeover,
        now=request.occurred_at + timedelta(minutes=2),
    )
    assert len(recovered) == 1
    assert isinstance(recovered[0], PaperCycleResult)
    assert recovered[0].status is PaperCycleStatus.FILLED
    assert restored.pending_operations == ()
    assert restored.latest_snapshot.operation_sequence == 1
    restored.close()


def test_single_writer_fencing_and_idempotency_conflict(tmp_path: Path) -> None:
    database = tmp_path / "fencing.db"
    runtime = _runtime(database)
    request = _request(runtime)
    first = runtime.acquire_lease(
        owner_id="worker-a",
        now=request.occurred_at,
        ttl=timedelta(minutes=5),
    )
    with pytest.raises(PaperLeaseConflictError, match="held"):
        runtime.acquire_lease(
            owner_id="worker-b",
            now=request.occurred_at + timedelta(minutes=1),
            ttl=timedelta(minutes=5),
        )
    second = runtime.acquire_lease(
        owner_id="worker-b",
        now=request.occurred_at + timedelta(minutes=6),
        ttl=timedelta(minutes=5),
    )
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(PaperLeaseConflictError, match="fenced"):
        runtime.run_cycle(
            request,
            lease=first,
            now=request.occurred_at + timedelta(minutes=6),
        )
    runtime.run_cycle(
        request,
        lease=second,
        now=request.occurred_at + timedelta(minutes=6),
    )
    conflicting = PaperCycleRequest(
        cycle_id=request.cycle_id,
        occurred_at=request.occurred_at,
        completed_bars=request.completed_bars,
        decision_quote=request.decision_quote,
        match_quote=request.match_quote,
        daily_pnl=Decimal("-1"),
    )
    with pytest.raises(PaperIdempotencyConflictError, match="different evidence"):
        runtime.run_cycle(
            conflicting,
            lease=second,
            now=request.occurred_at + timedelta(minutes=6),
        )
    runtime.close()


def test_pending_command_blocks_later_work_until_aborted(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "abort.db")
    first = _request(runtime, "cycle-1")
    second = _request(runtime, "cycle-2")
    lease = runtime.acquire_lease(
        owner_id="worker-a",
        now=first.occurred_at,
        ttl=timedelta(minutes=10),
    )
    runtime.stage_cycle(first, lease=lease, now=first.occurred_at)
    with pytest.raises(PaperRuntimeError, match="pending operation"):
        runtime.stage_cycle(second, lease=lease, now=first.occurred_at)
    runtime.abort_pending(
        first.cycle_id,
        reason="operator rejected stale upstream evidence",
        lease=lease,
        now=first.occurred_at + timedelta(minutes=1),
    )
    report = runtime.build_daily_report(first.occurred_at.date())
    assert report.healthy is False
    assert report.aborted_operation_count == 1
    runtime.run_cycle(
        second,
        lease=lease,
        now=first.occurred_at + timedelta(minutes=1),
    )
    runtime.close()


def test_snapshot_and_journal_tampering_are_detected(tmp_path: Path) -> None:
    snapshot_db = tmp_path / "snapshot-tamper.db"
    runtime = _runtime(snapshot_db)
    request = _request(runtime)
    lease = runtime.acquire_lease(
        owner_id="worker-a",
        now=request.occurred_at,
        ttl=timedelta(minutes=10),
    )
    runtime.run_cycle(request, lease=lease, now=request.occurred_at)
    runtime.close()
    connection = sqlite3.connect(snapshot_db)
    connection.execute(
        "UPDATE snapshots SET session_json = '{}' WHERE operation_sequence = 1"
    )
    connection.commit()
    connection.close()
    with pytest.raises(PaperRecoveryIntegrityError, match="session_json"):
        _runtime(snapshot_db)

    journal_db = tmp_path / "journal-tamper.db"
    runtime = _runtime(journal_db)
    runtime.close()
    connection = sqlite3.connect(journal_db)
    connection.execute("UPDATE journal SET payload_json = '{}' WHERE sequence = 1")
    connection.commit()
    connection.close()
    with pytest.raises(PaperRecoveryIntegrityError, match="digest"):
        _runtime(journal_db)


def test_runtime_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "identity.db"
    runtime = _runtime(database)
    runtime.close()
    with pytest.raises(PaperRecoveryIntegrityError, match="risk_policy_sha256"):
        _runtime(database, policy=_policy(max_order_notional=Decimal("999")))
