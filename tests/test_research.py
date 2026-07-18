from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.research import (
    CandidateSpec,
    ExplorationConfig,
    ExplorationEvaluation,
    ExplorationRunner,
    GuardResult,
    MetricValue,
    ValidationPlan,
    ValidationResult,
)

SHA1 = "a" * 40
SHA256 = "b" * 64


def _config() -> ExplorationConfig:
    return ExplorationConfig(
        run_id="carry-sweep-v1",
        dataset_ids=("dataset-v1",),
        code_revision=SHA1,
        candidates=(
            CandidateSpec.from_mapping("fast", "carry", {"window": 8}),
            CandidateSpec.from_mapping("slow", "carry", {"window": 24}),
            CandidateSpec.from_mapping("broken", "carry", {"window": 0}),
        ),
    )


def _evaluator(candidate: CandidateSpec) -> ExplorationEvaluation:
    params = dict(candidate.parameters)
    if params["window"] == "0":
        raise RuntimeError("window must be positive")
    look_ahead_ok = candidate.candidate_id != "fast"
    return ExplorationEvaluation(
        metrics=(MetricValue("net_return", Decimal("0.10")),),
        guards=(
            GuardResult(
                "look_ahead_regression",
                look_ahead_ok,
                (
                    "point-in-time replay matches batch positions"
                    if look_ahead_ok
                    else "future bar used"
                ),
            ),
        ),
    )


def test_runner_records_success_guard_failure_and_exception_deterministically() -> None:
    runner = ExplorationRunner()
    first = runner.run(_config(), _evaluator)
    second = runner.run(_config(), _evaluator)

    assert first.to_json() == second.to_json()
    assert first.content_sha256() == second.content_sha256()
    assert first.attempts[0].selection_eligible is False
    assert first.attempts[1].selection_eligible is True
    assert first.attempts[2].failure_type == "RuntimeError"
    assert first.attempts[2].failure_message == "window must be positive"


def test_guard_failed_or_failed_attempt_cannot_be_selected() -> None:
    report = ExplorationRunner().run(_config(), _evaluator)

    with pytest.raises(ValueError, match="guard-rejected"):
        report.record_selection(report.attempts[0].attempt_id, selected=True, reason="best")
    with pytest.raises(ValueError, match="failed"):
        report.record_selection(report.attempts[2].attempt_id, selected=True, reason="retry")


def test_validation_plan_freezes_selected_exploration_evidence() -> None:
    report = ExplorationRunner().run(_config(), _evaluator)
    selected = report.attempts[1].attempt_id
    report = report.record_selection(selected, selected=True, reason="best eligible net return")

    plan = ValidationPlan.from_exploration(
        plan_id="carry-validation-v1",
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
        report=report,
        selected_attempt_id=selected,
        split_policy_sha256=SHA256,
        acceptance_criteria_sha256="c" * 64,
        holdout_dataset_sha256="d" * 64,
    )

    assert plan.exploration_report_sha256 == report.content_sha256()
    assert plan.dataset_ids == report.config.dataset_ids
    assert plan.code_revision == report.config.code_revision
    assert len(plan.content_sha256()) == 64


def test_validation_requires_explicit_latest_selection() -> None:
    report = ExplorationRunner().run(_config(), _evaluator)
    selected = report.attempts[1].attempt_id
    report = report.record_selection(selected, selected=True, reason="shortlist")
    report = report.record_selection(selected, selected=False, reason="economic review rejected")

    with pytest.raises(ValueError, match="explicitly selected"):
        ValidationPlan.from_exploration(
            plan_id="carry-validation-v1",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            report=report,
            selected_attempt_id=selected,
            split_policy_sha256=SHA256,
            acceptance_criteria_sha256="c" * 64,
            holdout_dataset_sha256="d" * 64,
        )


def test_failed_validation_gate_blocks_promotion() -> None:
    result = ValidationResult(
        validation_plan_sha256=SHA256,
        metrics=(MetricValue("net_return", Decimal("0.02")),),
        gates=(
            GuardResult("cost_gate", True, "positive after costs"),
            GuardResult("concentration_gate", False, "one event dominates PnL"),
        ),
    )

    assert result.promotion_eligible is False
    with pytest.raises(ValueError, match="concentration_gate"):
        result.require_promotion_eligible()


def test_parameters_are_canonical_and_reject_non_finite_values() -> None:
    candidate = CandidateSpec.from_mapping(
        "candidate",
        "strategy",
        {"z": Decimal("1.20"), "a": True, "m": 3},
    )
    assert candidate.parameters == (("a", "true"), ("m", "3"), ("z", "1.20"))

    with pytest.raises(ValueError, match="finite"):
        CandidateSpec.from_mapping("bad", "strategy", {"x": Decimal("NaN")})
