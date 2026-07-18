from datetime import UTC, date, datetime, timedelta

import pytest

from quant_platform.strategy_decision import (
    ApprovalRecord,
    DecisionReason,
    DecisionReasonCode,
    DecisionReasonSeverity,
    DigestAlgorithm,
    GateResult,
    HoldoutSeal,
    PromotionGate,
    RevisionEvidence,
    RevisionKind,
    StrategyDecisionOutcome,
    StrategyDecisionPackage,
    StrategyLifecycleState,
    allowed_target_states,
    required_promotion_gate,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)
SHA = "a" * 64
CRITERIA = "b" * 64


def revisions() -> tuple[RevisionEvidence, ...]:
    return (
        RevisionEvidence(
            "dataset-1",
            RevisionKind.DATASET,
            "BTCUSDT immutable dataset",
            "dataset://btc-2023-2025",
            "dataset-v1",
            DigestAlgorithm.SHA256,
            "1" * 64,
        ),
        RevisionEvidence(
            "code-1",
            RevisionKind.CODE,
            "quant-alpha",
            "https://github.com/example/quant-alpha",
            "2" * 40,
            DigestAlgorithm.GIT_SHA1,
            "2" * 40,
        ),
        RevisionEvidence(
            "rule-1",
            RevisionKind.RULE,
            "tax and execution rules",
            "rule://research-v1",
            "rules-v1",
            DigestAlgorithm.SHA256,
            "3" * 64,
        ),
    )


def package(state: StrategyLifecycleState = StrategyLifecycleState.IDEA) -> StrategyDecisionPackage:
    return StrategyDecisionPackage(
        package_id="sdp-funding-carry-v1",
        strategy_id="funding_carry",
        package_version="1.0.0",
        hypothesis="Positive perpetual funding can compensate execution and basis risk.",
        state=state,
        created_at=NOW,
        updated_at=NOW,
        revisions=revisions(),
        known_limitations=("Crypto perpetual tax classification remains unresolved.",),
    )


def gate(
    result_id: str,
    gate_type: PromotionGate,
    when: datetime,
    *,
    passed: bool = True,
    criteria: str = SHA,
) -> GateResult:
    return GateResult(
        result_id=result_id,
        gate=gate_type,
        passed=passed,
        evaluated_at=when,
        evaluator="research-ci",
        criteria_sha256=criteria,
        evidence_ids=("dataset-1", "code-1", "rule-1"),
        summary=f"{gate_type.value} result",
    )


def approve(
    decision_id: str,
    from_state: StrategyLifecycleState,
    target_state: StrategyLifecycleState,
    gate_result_id: str,
    when: datetime,
) -> ApprovalRecord:
    return ApprovalRecord(
        decision_id=decision_id,
        from_state=from_state,
        target_state=target_state,
        outcome=StrategyDecisionOutcome.APPROVED,
        decided_at=when,
        decided_by="research-owner",
        gate_result_ids=(gate_result_id,),
        rationale="Gate passed with pinned evidence.",
    )


def test_valid_state_machine_reaches_holdout_validated() -> None:
    current = package()
    implementation_time = NOW + timedelta(minutes=1)
    current = current.add_gate_result(
        gate("gate-implementation", PromotionGate.IMPLEMENTATION, implementation_time),
        updated_at=implementation_time,
    )
    decision_time = implementation_time + timedelta(minutes=1)
    current = current.apply_decision(
        approve(
            "decision-implemented",
            StrategyLifecycleState.IDEA,
            StrategyLifecycleState.IMPLEMENTED,
            "gate-implementation",
            decision_time,
        ),
        updated_at=decision_time,
    )

    development_time = decision_time + timedelta(minutes=1)
    current = current.add_gate_result(
        gate("gate-development", PromotionGate.DEVELOPMENT_VALIDATION, development_time),
        updated_at=development_time,
    )
    decision_time = development_time + timedelta(minutes=1)
    current = current.apply_decision(
        approve(
            "decision-development",
            StrategyLifecycleState.IMPLEMENTED,
            StrategyLifecycleState.DEVELOPMENT_VALIDATED,
            "gate-development",
            decision_time,
        ),
        updated_at=decision_time,
    )

    seal_time = decision_time + timedelta(minutes=1)
    current = current.seal_holdout(
        HoldoutSeal(
            seal_id="holdout-2025",
            dataset_evidence_id="dataset-1",
            development_end=date(2024, 12, 31),
            holdout_start=date(2025, 1, 1),
            holdout_end=date(2025, 12, 31),
            split_spec_sha256="4" * 64,
            acceptance_criteria_sha256=CRITERIA,
            sealed_at=seal_time,
        ),
        updated_at=seal_time,
    )
    holdout_time = seal_time + timedelta(minutes=1)
    current = current.add_gate_result(
        gate(
            "gate-holdout",
            PromotionGate.HOLDOUT_VALIDATION,
            holdout_time,
            criteria=CRITERIA,
        ),
        updated_at=holdout_time,
    )
    decision_time = holdout_time + timedelta(minutes=1)
    current = current.apply_decision(
        approve(
            "decision-holdout",
            StrategyLifecycleState.DEVELOPMENT_VALIDATED,
            StrategyLifecycleState.HOLDOUT_VALIDATED,
            "gate-holdout",
            decision_time,
        ),
        updated_at=decision_time,
    )

    assert current.state is StrategyLifecycleState.HOLDOUT_VALIDATED
    assert current.holdout_seal is not None
    assert current.holdout_seal.acceptance_criteria_sha256 == CRITERIA


def test_illegal_skip_and_failed_gate_are_rejected() -> None:
    current = package().add_gate_result(
        gate("gate-implementation", PromotionGate.IMPLEMENTATION, NOW + timedelta(minutes=1)),
        updated_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        current.apply_decision(
            approve(
                "decision-skip",
                StrategyLifecycleState.IDEA,
                StrategyLifecycleState.HOLDOUT_VALIDATED,
                "gate-implementation",
                NOW + timedelta(minutes=2),
            ),
            updated_at=NOW + timedelta(minutes=2),
        )

    failed = package().add_gate_result(
        gate(
            "gate-failed",
            PromotionGate.IMPLEMENTATION,
            NOW + timedelta(minutes=1),
            passed=False,
        ),
        updated_at=NOW + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="passing gate"):
        failed.apply_decision(
            approve(
                "decision-failed",
                StrategyLifecycleState.IDEA,
                StrategyLifecycleState.IMPLEMENTED,
                "gate-failed",
                NOW + timedelta(minutes=2),
            ),
            updated_at=NOW + timedelta(minutes=2),
        )


def test_held_decision_requires_and_preserves_blocking_reason() -> None:
    current = package().add_gate_result(
        gate(
            "gate-development",
            PromotionGate.IMPLEMENTATION,
            NOW + timedelta(minutes=1),
            passed=False,
        ),
        updated_at=NOW + timedelta(minutes=1),
    )
    reason = DecisionReason(
        reason_id="reason-economic",
        code=DecisionReasonCode.ECONOMICALLY_UNVIABLE,
        severity=DecisionReasonSeverity.BLOCKING,
        detail="All development folds are negative after costs.",
        evidence_ids=("dataset-1",),
    )
    decision = ApprovalRecord(
        decision_id="decision-held",
        from_state=StrategyLifecycleState.IDEA,
        target_state=StrategyLifecycleState.IMPLEMENTED,
        outcome=StrategyDecisionOutcome.HELD,
        decided_at=NOW + timedelta(minutes=2),
        decided_by="research-owner",
        gate_result_ids=("gate-development",),
        reasons=(reason,),
    )
    current = current.apply_decision(decision, updated_at=NOW + timedelta(minutes=2))

    assert current.state is StrategyLifecycleState.IDEA
    assert current.decisions[-1].reasons == (reason,)


def test_holdout_policy_cannot_be_replaced_or_evaluated_with_new_criteria() -> None:
    current = package(StrategyLifecycleState.DEVELOPMENT_VALIDATED)
    seal = HoldoutSeal(
        seal_id="holdout-2025",
        dataset_evidence_id="dataset-1",
        development_end=date(2024, 12, 31),
        holdout_start=date(2025, 1, 1),
        holdout_end=date(2025, 12, 31),
        split_spec_sha256="4" * 64,
        acceptance_criteria_sha256=CRITERIA,
        sealed_at=NOW + timedelta(minutes=1),
    )
    current = current.seal_holdout(seal, updated_at=NOW + timedelta(minutes=1))

    with pytest.raises(ValueError, match="already sealed"):
        current.seal_holdout(seal, updated_at=NOW + timedelta(minutes=2))
    with pytest.raises(ValueError, match="sealed acceptance criteria"):
        current.add_gate_result(
            gate(
                "gate-holdout-mutated",
                PromotionGate.HOLDOUT_VALIDATION,
                NOW + timedelta(minutes=2),
                criteria="5" * 64,
            ),
            updated_at=NOW + timedelta(minutes=2),
        )


def test_development_validated_requires_data_code_and_rule_revisions() -> None:
    with pytest.raises(ValueError, match="DATASET"):
        StrategyDecisionPackage(
            package_id="missing-evidence",
            strategy_id="example",
            package_version="1",
            hypothesis="Example hypothesis",
            state=StrategyLifecycleState.DEVELOPMENT_VALIDATED,
            created_at=NOW,
            updated_at=NOW,
            revisions=(revisions()[1],),
        )


def test_json_and_markdown_are_deterministic_and_human_readable() -> None:
    current = package()

    assert current.to_json() == current.to_json()
    assert current.content_sha256() == current.content_sha256()
    assert '"strategy_id": "funding_carry"' in current.to_json()
    markdown = current.to_markdown()
    assert "Strategy Decision Package" in markdown
    assert "Immutable revisions" in markdown
    assert current.content_sha256() in markdown


def test_retired_is_terminal() -> None:
    current = package().add_gate_result(
        gate("gate-retire", PromotionGate.RETIREMENT, NOW + timedelta(minutes=1)),
        updated_at=NOW + timedelta(minutes=1),
    )
    retired = current.apply_decision(
        approve(
            "decision-retire",
            StrategyLifecycleState.IDEA,
            StrategyLifecycleState.RETIRED,
            "gate-retire",
            NOW + timedelta(minutes=2),
        ),
        updated_at=NOW + timedelta(minutes=2),
    )
    with pytest.raises(ValueError, match="terminal"):
        retired.apply_decision(
            approve(
                "decision-after-retire",
                StrategyLifecycleState.RETIRED,
                StrategyLifecycleState.LIVE,
                "gate-retire",
                NOW + timedelta(minutes=3),
            ),
            updated_at=NOW + timedelta(minutes=3),
        )


def test_transition_introspection_exposes_only_legal_targets() -> None:
    assert required_promotion_gate(
        StrategyLifecycleState.PAPER, StrategyLifecycleState.LIVE_CANDIDATE
    ) is PromotionGate.PAPER_RECONCILIATION
    assert allowed_target_states(StrategyLifecycleState.IDEA) == (
        StrategyLifecycleState.IMPLEMENTED,
        StrategyLifecycleState.RETIRED,
    )
    assert allowed_target_states(StrategyLifecycleState.RETIRED) == ()
