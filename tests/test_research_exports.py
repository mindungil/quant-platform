from quant_platform import (
    CandidateSelection,
    CandidateSpec,
    ExplorationAttempt,
    ExplorationAttemptStatus,
    ExplorationConfig,
    ExplorationEvaluation,
    ExplorationReport,
    ExplorationRunner,
    GuardResult,
    MetricValue,
    ValidationPlan,
    ValidationResult,
)


def test_research_workbench_types_are_public() -> None:
    exported = (
        CandidateSelection,
        CandidateSpec,
        ExplorationAttempt,
        ExplorationAttemptStatus,
        ExplorationConfig,
        ExplorationEvaluation,
        ExplorationReport,
        ExplorationRunner,
        GuardResult,
        MetricValue,
        ValidationPlan,
        ValidationResult,
    )
    assert all(item.__module__.startswith("quant_platform") for item in exported)
