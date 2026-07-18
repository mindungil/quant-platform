"""Deterministic exploration and validation workflow contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from string import hexdigits


class ExplorationAttemptStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CandidateSpec:
    candidate_id: str
    strategy_id: str
    parameters: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_text(self.candidate_id, "candidate_id")
        _require_text(self.strategy_id, "strategy_id")
        names = tuple(name for name, _ in self.parameters)
        if names != tuple(sorted(names)):
            raise ValueError("candidate parameters must be sorted by name")
        _require_unique_text(names, "parameter names", allow_empty=True)
        for name, value in self.parameters:
            _require_text(name, "parameter name")
            _require_text(value, f"parameter {name}")

    @classmethod
    def from_mapping(
        cls,
        candidate_id: str,
        strategy_id: str,
        parameters: Mapping[str, object],
    ) -> CandidateSpec:
        normalized = tuple(
            sorted((str(name), _parameter_text(value)) for name, value in parameters.items())
        )
        return cls(candidate_id, strategy_id, normalized)


@dataclass(frozen=True, slots=True)
class MetricValue:
    name: str
    value: Decimal

    def __post_init__(self) -> None:
        _require_text(self.name, "metric name")
        if not self.value.is_finite():
            raise ValueError("metric value must be finite")


@dataclass(frozen=True, slots=True)
class GuardResult:
    guard_id: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        _require_text(self.guard_id, "guard_id")
        _require_text(self.detail, "guard detail")


@dataclass(frozen=True, slots=True)
class ExplorationEvaluation:
    metrics: tuple[MetricValue, ...]
    guards: tuple[GuardResult, ...]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_unique_text(tuple(item.name for item in self.metrics), "metric names")
        _require_unique_text(tuple(item.guard_id for item in self.guards), "guard IDs")
        _require_unique_text(self.notes, "evaluation notes", allow_empty=True)

    @property
    def selection_eligible(self) -> bool:
        return all(guard.passed for guard in self.guards)


@dataclass(frozen=True, slots=True)
class ExplorationConfig:
    run_id: str
    dataset_ids: tuple[str, ...]
    code_revision: str
    candidates: tuple[CandidateSpec, ...]

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        _require_unique_text(self.dataset_ids, "dataset IDs")
        _require_git_sha(self.code_revision, "code_revision")
        _require_unique_text(
            tuple(candidate.candidate_id for candidate in self.candidates),
            "candidate IDs",
        )


@dataclass(frozen=True, slots=True)
class ExplorationAttempt:
    attempt_id: str
    ordinal: int
    candidate: CandidateSpec
    status: ExplorationAttemptStatus
    evaluation: ExplorationEvaluation | None = None
    failure_type: str | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, "attempt_id")
        if self.ordinal < 0:
            raise ValueError("attempt ordinal must be non-negative")
        if self.status is ExplorationAttemptStatus.SUCCEEDED:
            if self.evaluation is None:
                raise ValueError("successful attempt requires an evaluation")
            if self.failure_type is not None or self.failure_message is not None:
                raise ValueError("successful attempt must not contain failure fields")
        else:
            if self.evaluation is not None:
                raise ValueError("failed attempt must not contain an evaluation")
            if self.failure_type is None or self.failure_message is None:
                raise ValueError("failed attempt requires failure type and message")
            _require_text(self.failure_type, "failure_type")
            _require_text(self.failure_message, "failure_message")

    @property
    def selection_eligible(self) -> bool:
        return (
            self.status is ExplorationAttemptStatus.SUCCEEDED
            and self.evaluation is not None
            and self.evaluation.selection_eligible
        )


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    decision_id: str
    attempt_id: str
    selected: bool
    reason: str

    def __post_init__(self) -> None:
        for name in ("decision_id", "attempt_id", "reason"):
            _require_text(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ExplorationReport:
    config: ExplorationConfig
    attempts: tuple[ExplorationAttempt, ...]
    selections: tuple[CandidateSelection, ...] = ()

    def __post_init__(self) -> None:
        if len(self.attempts) != len(self.config.candidates):
            raise ValueError("one exploration attempt is required per candidate")
        expected_candidates = tuple(candidate.candidate_id for candidate in self.config.candidates)
        actual_candidates = tuple(attempt.candidate.candidate_id for attempt in self.attempts)
        if actual_candidates != expected_candidates:
            raise ValueError("attempt order must match candidate order")
        if tuple(attempt.ordinal for attempt in self.attempts) != tuple(range(len(self.attempts))):
            raise ValueError("attempt ordinals must be contiguous and ordered")
        _require_unique_text(tuple(attempt.attempt_id for attempt in self.attempts), "attempt IDs")
        _require_unique_text(
            tuple(selection.decision_id for selection in self.selections),
            "selection decision IDs",
            allow_empty=True,
        )
        attempt_ids = {attempt.attempt_id for attempt in self.attempts}
        if any(selection.attempt_id not in attempt_ids for selection in self.selections):
            raise ValueError("selection references an unknown attempt")

    def record_selection(
        self,
        attempt_id: str,
        *,
        selected: bool,
        reason: str,
    ) -> ExplorationReport:
        attempt = self.get_attempt(attempt_id)
        if selected and not attempt.selection_eligible:
            raise ValueError("failed or guard-rejected attempts cannot be selected")
        decision = CandidateSelection(
            decision_id=f"{self.config.run_id}:selection:{len(self.selections):04d}",
            attempt_id=attempt_id,
            selected=selected,
            reason=reason,
        )
        return replace(self, selections=self.selections + (decision,))

    def get_attempt(self, attempt_id: str) -> ExplorationAttempt:
        for attempt in self.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        raise KeyError(attempt_id)

    def is_selected(self, attempt_id: str) -> bool:
        self.get_attempt(attempt_id)
        for selection in reversed(self.selections):
            if selection.attempt_id == attempt_id:
                return selection.selected
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "config": {
                "run_id": self.config.run_id,
                "dataset_ids": list(self.config.dataset_ids),
                "code_revision": self.config.code_revision,
                "candidates": [_candidate_dict(item) for item in self.config.candidates],
            },
            "attempts": [_attempt_dict(item) for item in self.attempts],
            "selections": [
                {
                    "decision_id": item.decision_id,
                    "attempt_id": item.attempt_id,
                    "selected": item.selected,
                    "reason": item.reason,
                }
                for item in self.selections
            ],
        }

    def to_json(self) -> str:
        return _deterministic_json(self.to_dict())

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


EvaluationFunction = Callable[[CandidateSpec], ExplorationEvaluation]


class ExplorationRunner:
    def run(
        self,
        config: ExplorationConfig,
        evaluator: EvaluationFunction,
    ) -> ExplorationReport:
        attempts: list[ExplorationAttempt] = []
        for ordinal, candidate in enumerate(config.candidates):
            attempt_id = f"{config.run_id}:attempt:{ordinal:04d}:{candidate.candidate_id}"
            try:
                evaluation = evaluator(candidate)
            except Exception as exc:  # evaluator boundary intentionally records failures
                message = str(exc).strip() or "evaluation failed without a message"
                attempts.append(
                    ExplorationAttempt(
                        attempt_id=attempt_id,
                        ordinal=ordinal,
                        candidate=candidate,
                        status=ExplorationAttemptStatus.FAILED,
                        failure_type=type(exc).__name__,
                        failure_message=message,
                    )
                )
            else:
                attempts.append(
                    ExplorationAttempt(
                        attempt_id=attempt_id,
                        ordinal=ordinal,
                        candidate=candidate,
                        status=ExplorationAttemptStatus.SUCCEEDED,
                        evaluation=evaluation,
                    )
                )
        return ExplorationReport(config=config, attempts=tuple(attempts))


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    plan_id: str
    created_at: datetime
    exploration_report_sha256: str
    selected_attempt_id: str
    dataset_ids: tuple[str, ...]
    code_revision: str
    split_policy_sha256: str
    acceptance_criteria_sha256: str
    holdout_dataset_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.plan_id, "plan_id")
        _require_aware(self.created_at, "created_at")
        _require_sha256(self.exploration_report_sha256, "exploration_report_sha256")
        _require_text(self.selected_attempt_id, "selected_attempt_id")
        _require_unique_text(self.dataset_ids, "dataset IDs")
        _require_git_sha(self.code_revision, "code_revision")
        for name in (
            "split_policy_sha256",
            "acceptance_criteria_sha256",
            "holdout_dataset_sha256",
        ):
            _require_sha256(getattr(self, name), name)

    @classmethod
    def from_exploration(
        cls,
        *,
        plan_id: str,
        created_at: datetime,
        report: ExplorationReport,
        selected_attempt_id: str,
        split_policy_sha256: str,
        acceptance_criteria_sha256: str,
        holdout_dataset_sha256: str,
    ) -> ValidationPlan:
        attempt = report.get_attempt(selected_attempt_id)
        if not report.is_selected(selected_attempt_id):
            raise ValueError("validation requires an explicitly selected exploration attempt")
        if not attempt.selection_eligible:
            raise ValueError("validation cannot seal a failed or guard-rejected attempt")
        return cls(
            plan_id=plan_id,
            created_at=created_at,
            exploration_report_sha256=report.content_sha256(),
            selected_attempt_id=selected_attempt_id,
            dataset_ids=report.config.dataset_ids,
            code_revision=report.config.code_revision,
            split_policy_sha256=split_policy_sha256,
            acceptance_criteria_sha256=acceptance_criteria_sha256,
            holdout_dataset_sha256=holdout_dataset_sha256,
        )

    def to_json(self) -> str:
        return _deterministic_json(
            {
                "plan_id": self.plan_id,
                "created_at": self.created_at.isoformat(),
                "exploration_report_sha256": self.exploration_report_sha256,
                "selected_attempt_id": self.selected_attempt_id,
                "dataset_ids": list(self.dataset_ids),
                "code_revision": self.code_revision,
                "split_policy_sha256": self.split_policy_sha256,
                "acceptance_criteria_sha256": self.acceptance_criteria_sha256,
                "holdout_dataset_sha256": self.holdout_dataset_sha256,
            }
        )

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ValidationResult:
    validation_plan_sha256: str
    metrics: tuple[MetricValue, ...]
    gates: tuple[GuardResult, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.validation_plan_sha256, "validation_plan_sha256")
        _require_unique_text(tuple(item.name for item in self.metrics), "metric names")
        _require_unique_text(tuple(item.guard_id for item in self.gates), "gate IDs")

    @property
    def promotion_eligible(self) -> bool:
        return bool(self.gates) and all(gate.passed for gate in self.gates)

    def require_promotion_eligible(self) -> None:
        if not self.promotion_eligible:
            failed = tuple(gate.guard_id for gate in self.gates if not gate.passed)
            raise ValueError(f"validation is not promotion eligible; failed gates: {failed}")


def _candidate_dict(candidate: CandidateSpec) -> dict[str, object]:
    return {
        "candidate_id": candidate.candidate_id,
        "strategy_id": candidate.strategy_id,
        "parameters": {name: value for name, value in candidate.parameters},
    }


def _attempt_dict(attempt: ExplorationAttempt) -> dict[str, object]:
    evaluation: dict[str, object] | None = None
    if attempt.evaluation is not None:
        evaluation = {
            "metrics": [
                {"name": item.name, "value": str(item.value)}
                for item in attempt.evaluation.metrics
            ],
            "guards": [
                {
                    "guard_id": item.guard_id,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in attempt.evaluation.guards
            ],
            "notes": list(attempt.evaluation.notes),
        }
    return {
        "attempt_id": attempt.attempt_id,
        "ordinal": attempt.ordinal,
        "candidate": _candidate_dict(attempt.candidate),
        "status": attempt.status.value,
        "evaluation": evaluation,
        "failure_type": attempt.failure_type,
        "failure_message": attempt.failure_message,
    }


def _parameter_text(value: object) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("candidate Decimal parameters must be finite")
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int)):
        text = str(value)
        _require_text(text, "parameter value")
        return text
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("candidate float parameters must be finite")
        return repr(value)
    raise TypeError(f"unsupported candidate parameter type: {type(value).__name__}")


def _deterministic_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_unique_text(
    values: tuple[str, ...],
    name: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{name} must not be empty")
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty values")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in hexdigits for character in normalized):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256 digest")


def _require_git_sha(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 40 or any(character not in hexdigits for character in normalized):
        raise ValueError(f"{name} must be a 40-character hexadecimal Git SHA")
