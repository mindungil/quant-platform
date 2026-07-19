from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant_platform.comparison_report import (
    CandidateComparison,
    ComparisonCostLine,
    ParameterSearchSpace,
    StrategyComparisonReport,
    TaxEstimateEvidence,
)
from quant_platform.finance import TaxConfidence
from quant_platform.research import (
    CandidateSpec,
    ExplorationConfig,
    ExplorationEvaluation,
    ExplorationRunner,
    GuardResult,
    MetricValue,
    ValidationPlan,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _exploration():  # type: ignore[no-untyped-def]
    config = ExplorationConfig(
        run_id="exploration-v1",
        dataset_ids=("btc-2024-1h",),
        code_revision="a" * 40,
        candidates=(
            CandidateSpec.from_mapping("momentum", "momentum", {"lookback": 24}),
            CandidateSpec.from_mapping("reversion", "reversion", {"zscore": 2}),
            CandidateSpec.from_mapping("carry", "carry", {"threshold": "0.001"}),
        ),
    )

    def evaluate(candidate: CandidateSpec) -> ExplorationEvaluation:
        return ExplorationEvaluation(
            metrics=(MetricValue("net", Decimal(len(candidate.strategy_id))),),
            guards=(GuardResult("causal", True, "passed"),),
        )

    report = ExplorationRunner().run(config, evaluate)
    return report.record_selection(
        report.attempts[1].attempt_id,
        selected=True,
        reason="best OOS result",
    )


def _candidate(attempt, hypothesis: str, parameter: str, values: tuple[str, ...], gross: str):  # type: ignore[no-untyped-def]
    return CandidateComparison(
        attempt_id=attempt.attempt_id,
        candidate_id=attempt.candidate.candidate_id,
        strategy_id=attempt.candidate.strategy_id,
        economic_hypothesis=hypothesis,
        exploration_trials=1,
        parameter_search=(ParameterSearchSpace(parameter, values),),
        gross_pnl=Decimal(gross),
        costs=(
            ComparisonCostLine("commission", "Commission", Decimal("4")),
            ComparisonCostLine("slippage", "Slippage", Decimal("5")),
        ),
        benchmark_pnl=Decimal("12"),
        oos_return=Decimal("0.08"),
        turnover=Decimal("3.5"),
        event_concentration=Decimal("0.20"),
        capacity_limit_notional=Decimal("100000"),
        tax=TaxEstimateEvidence(
            rule_version="tax-rule-v1",
            confidence=TaxConfidence.CONFIRMED,
            estimated_tax=Decimal("8"),
        ),
        guards=(GuardResult("oos-positive", True, "passed"),),
    )


def _candidates(report):  # type: ignore[no-untyped-def]
    return (
        _candidate(report.attempts[0], "returns persist", "lookback", ("12", "24", "48"), "100"),
        _candidate(report.attempts[1], "dislocations revert", "zscore", ("1", "2", "3"), "95"),
        _candidate(report.attempts[2], "carry compensates risk", "threshold", ("0", "0.001", "0.002"), "20"),
    )


def _comparison():  # type: ignore[no-untyped-def]
    exploration = _exploration()
    return StrategyComparisonReport.from_exploration(
        report_id="comparison-v1",
        created_at=T0,
        exploration=exploration,
        cost_scenario_id="cost-v1",
        cost_scenario_sha256="b" * 64,
        benchmark_id="buy-and-hold",
        benchmark_revision="benchmark-v1",
        candidates=_candidates(exploration),
        selected_attempt_id=exploration.attempts[1].attempt_id,
        selection_reason="best result after costs",
    )


def test_report_waterfall_and_outputs_are_deterministic() -> None:
    first = _comparison()
    second = _comparison()

    assert first.candidates[0].total_cost == Decimal("9")
    assert first.candidates[0].economic_net_pnl == Decimal("91")
    assert first.candidates[0].estimated_after_tax_pnl == Decimal("83")
    assert first.to_json() == second.to_json()
    assert first.to_markdown() == second.to_markdown()
    assert first.content_sha256() == second.content_sha256()
    assert "## Cost waterfalls" in first.to_markdown()


def test_only_selected_candidate_can_bind_validation() -> None:
    exploration = _exploration()
    comparison = StrategyComparisonReport.from_exploration(
        report_id="comparison-v1",
        created_at=T0,
        exploration=exploration,
        cost_scenario_id="cost-v1",
        cost_scenario_sha256="b" * 64,
        benchmark_id="buy-and-hold",
        benchmark_revision="benchmark-v1",
        candidates=_candidates(exploration),
        selected_attempt_id=exploration.attempts[1].attempt_id,
        selection_reason="selected before validation",
    )
    plan = ValidationPlan.from_exploration(
        plan_id="validation-v1",
        created_at=T0,
        report=exploration,
        selected_attempt_id=exploration.attempts[1].attempt_id,
        split_policy_sha256="c" * 64,
        acceptance_criteria_sha256="d" * 64,
        holdout_dataset_sha256="e" * 64,
    )

    assert comparison.bind_validation_plan(plan).validation_plan_sha256 == plan.content_sha256()

    wrong = ValidationPlan(
        plan_id="wrong",
        created_at=T0,
        exploration_report_sha256=exploration.content_sha256(),
        selected_attempt_id=exploration.attempts[0].attempt_id,
        dataset_ids=exploration.config.dataset_ids,
        code_revision=exploration.config.code_revision,
        split_policy_sha256="c" * 64,
        acceptance_criteria_sha256="d" * 64,
        holdout_dataset_sha256="e" * 64,
    )
    with pytest.raises(ValueError, match="selected comparison"):
        comparison.bind_validation_plan(wrong)
