"""Deterministic, evidence-backed strategy comparison reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from string import hexdigits

from .finance import TaxConfidence
from .research import (
    ExplorationAttemptStatus,
    ExplorationReport,
    GuardResult,
    ValidationPlan,
)

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class ParameterSearchSpace:
    name: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.name, "parameter name")
        _unique_text(self.values, f"search values for {self.name}")
        if self.values != tuple(sorted(self.values)):
            raise ValueError("parameter search values must be sorted")


@dataclass(frozen=True, slots=True)
class ComparisonCostLine:
    cost_id: str
    label: str
    amount: Decimal

    def __post_init__(self) -> None:
        _text(self.cost_id, "cost_id")
        _text(self.label, "cost label")
        _finite(self.amount, "cost amount")


@dataclass(frozen=True, slots=True)
class TaxEstimateEvidence:
    rule_version: str
    confidence: TaxConfidence
    estimated_tax: Decimal
    note: str = ""

    def __post_init__(self) -> None:
        _text(self.rule_version, "tax rule_version")
        _nonnegative(self.estimated_tax, "estimated_tax")
        if self.confidence is TaxConfidence.REVIEW_REQUIRED:
            _text(self.note, "tax review note")

    @property
    def review_required(self) -> bool:
        return self.confidence is TaxConfidence.REVIEW_REQUIRED


@dataclass(frozen=True, slots=True)
class CandidateComparison:
    attempt_id: str
    candidate_id: str
    strategy_id: str
    economic_hypothesis: str
    exploration_trials: int
    parameter_search: tuple[ParameterSearchSpace, ...]
    gross_pnl: Decimal
    costs: tuple[ComparisonCostLine, ...]
    benchmark_pnl: Decimal
    oos_return: Decimal
    turnover: Decimal
    event_concentration: Decimal
    capacity_limit_notional: Decimal | None
    tax: TaxEstimateEvidence
    guards: tuple[GuardResult, ...]

    def __post_init__(self) -> None:
        for name in (
            "attempt_id",
            "candidate_id",
            "strategy_id",
            "economic_hypothesis",
        ):
            _text(getattr(self, name), name)
        if self.exploration_trials <= 0:
            raise ValueError("exploration_trials must be positive")
        parameter_names = tuple(item.name for item in self.parameter_search)
        _unique_text(parameter_names, "parameter search names", allow_empty=True)
        if parameter_names != tuple(sorted(parameter_names)):
            raise ValueError("parameter search spaces must be sorted by name")
        cost_ids = tuple(item.cost_id for item in self.costs)
        _unique_text(cost_ids, "cost IDs", allow_empty=True)
        if cost_ids != tuple(sorted(cost_ids)):
            raise ValueError("cost lines must be sorted by cost_id")
        guard_ids = tuple(item.guard_id for item in self.guards)
        _unique_text(guard_ids, "comparison guard IDs")
        for name in ("gross_pnl", "benchmark_pnl", "oos_return"):
            _finite(getattr(self, name), name)
        _nonnegative(self.turnover, "turnover")
        _unit_interval(self.event_concentration, "event_concentration")
        if self.capacity_limit_notional is not None:
            _positive(self.capacity_limit_notional, "capacity_limit_notional")

    @property
    def total_cost(self) -> Decimal:
        return sum((line.amount for line in self.costs), start=ZERO)

    @property
    def economic_net_pnl(self) -> Decimal:
        return self.gross_pnl - self.total_cost

    @property
    def estimated_after_tax_pnl(self) -> Decimal:
        return self.economic_net_pnl - self.tax.estimated_tax

    @property
    def selection_eligible(self) -> bool:
        return bool(self.guards) and all(guard.passed for guard in self.guards)

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "benchmark_pnl": str(self.benchmark_pnl),
            "candidate_id": self.candidate_id,
            "capacity_limit_notional": (
                None
                if self.capacity_limit_notional is None
                else str(self.capacity_limit_notional)
            ),
            "costs": [
                {
                    "amount": str(line.amount),
                    "cost_id": line.cost_id,
                    "label": line.label,
                }
                for line in self.costs
            ],
            "economic_hypothesis": self.economic_hypothesis,
            "economic_net_pnl": str(self.economic_net_pnl),
            "estimated_after_tax_pnl": str(self.estimated_after_tax_pnl),
            "event_concentration": str(self.event_concentration),
            "exploration_trials": self.exploration_trials,
            "gross_pnl": str(self.gross_pnl),
            "guards": [
                {
                    "detail": guard.detail,
                    "guard_id": guard.guard_id,
                    "passed": guard.passed,
                }
                for guard in self.guards
            ],
            "oos_return": str(self.oos_return),
            "parameter_search": [
                {"name": item.name, "values": list(item.values)}
                for item in self.parameter_search
            ],
            "selection_eligible": self.selection_eligible,
            "strategy_id": self.strategy_id,
            "tax": {
                "confidence": self.tax.confidence.value,
                "estimated_tax": str(self.tax.estimated_tax),
                "note": self.tax.note,
                "review_required": self.tax.review_required,
                "rule_version": self.tax.rule_version,
            },
            "total_cost": str(self.total_cost),
            "turnover": str(self.turnover),
        }


@dataclass(frozen=True, slots=True)
class StrategyComparisonReport:
    report_id: str
    created_at: datetime
    exploration_run_id: str
    exploration_report_sha256: str
    dataset_ids: tuple[str, ...]
    code_revision: str
    cost_scenario_id: str
    cost_scenario_sha256: str
    benchmark_id: str
    benchmark_revision: str
    candidates: tuple[CandidateComparison, ...]
    selected_attempt_id: str
    selection_reason: str
    validation_plan_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "report_id",
            "exploration_run_id",
            "cost_scenario_id",
            "benchmark_id",
            "benchmark_revision",
            "selected_attempt_id",
            "selection_reason",
        ):
            _text(getattr(self, name), name)
        _aware(self.created_at, "created_at")
        _sha256(self.exploration_report_sha256, "exploration_report_sha256")
        _git_sha(self.code_revision, "code_revision")
        _sha256(self.cost_scenario_sha256, "cost_scenario_sha256")
        if self.validation_plan_sha256 is not None:
            _sha256(self.validation_plan_sha256, "validation_plan_sha256")
        _unique_text(self.dataset_ids, "dataset IDs")
        if len(self.candidates) < 3:
            raise ValueError("comparison report requires at least three candidates")
        attempt_ids = tuple(candidate.attempt_id for candidate in self.candidates)
        _unique_text(attempt_ids, "comparison attempt IDs")
        _unique_text(
            tuple(candidate.candidate_id for candidate in self.candidates),
            "comparison candidate IDs",
        )
        _unique_text(
            tuple(candidate.economic_hypothesis for candidate in self.candidates),
            "economic hypotheses",
        )
        if self.selected_attempt_id not in attempt_ids:
            raise ValueError("selected attempt is not present in the comparison")
        if not self.selected_candidate.selection_eligible:
            raise ValueError("selected comparison candidate must pass all comparison guards")

    @property
    def selected_candidate(self) -> CandidateComparison:
        for candidate in self.candidates:
            if candidate.attempt_id == self.selected_attempt_id:
                return candidate
        raise KeyError(self.selected_attempt_id)

    @classmethod
    def from_exploration(
        cls,
        *,
        report_id: str,
        created_at: datetime,
        exploration: ExplorationReport,
        cost_scenario_id: str,
        cost_scenario_sha256: str,
        benchmark_id: str,
        benchmark_revision: str,
        candidates: tuple[CandidateComparison, ...],
        selected_attempt_id: str,
        selection_reason: str,
    ) -> StrategyComparisonReport:
        current_selected = tuple(
            attempt.attempt_id
            for attempt in exploration.attempts
            if exploration.is_selected(attempt.attempt_id)
        )
        if current_selected != (selected_attempt_id,):
            raise ValueError("exploration must have exactly one currently selected attempt")
        ordinal_by_attempt: dict[str, int] = {}
        trials_by_strategy: dict[str, int] = {}
        for attempt in exploration.attempts:
            ordinal_by_attempt[attempt.attempt_id] = attempt.ordinal
            strategy_id = attempt.candidate.strategy_id
            trials_by_strategy[strategy_id] = trials_by_strategy.get(strategy_id, 0) + 1
        ordinals: list[int] = []
        for comparison in candidates:
            attempt = exploration.get_attempt(comparison.attempt_id)
            if attempt.status is not ExplorationAttemptStatus.SUCCEEDED:
                raise ValueError("comparison candidates require successful exploration attempts")
            if attempt.candidate.candidate_id != comparison.candidate_id:
                raise ValueError("comparison candidate_id does not match exploration")
            if attempt.candidate.strategy_id != comparison.strategy_id:
                raise ValueError("comparison strategy_id does not match exploration")
            if comparison.exploration_trials != trials_by_strategy[comparison.strategy_id]:
                raise ValueError("exploration trial count does not match report disclosure")
            _require_parameters_disclosed(attempt.candidate.parameters, comparison.parameter_search)
            ordinals.append(ordinal_by_attempt[comparison.attempt_id])
        if ordinals != sorted(ordinals):
            raise ValueError("comparison candidates must follow exploration attempt order")
        return cls(
            report_id=report_id,
            created_at=created_at,
            exploration_run_id=exploration.config.run_id,
            exploration_report_sha256=exploration.content_sha256(),
            dataset_ids=exploration.config.dataset_ids,
            code_revision=exploration.config.code_revision,
            cost_scenario_id=cost_scenario_id,
            cost_scenario_sha256=cost_scenario_sha256,
            benchmark_id=benchmark_id,
            benchmark_revision=benchmark_revision,
            candidates=candidates,
            selected_attempt_id=selected_attempt_id,
            selection_reason=selection_reason,
        )

    def bind_validation_plan(self, plan: ValidationPlan) -> StrategyComparisonReport:
        if plan.exploration_report_sha256 != self.exploration_report_sha256:
            raise ValueError("validation plan references a different exploration report")
        if plan.selected_attempt_id != self.selected_attempt_id:
            raise ValueError("validation plan must reference the selected comparison candidate")
        if plan.dataset_ids != self.dataset_ids:
            raise ValueError("validation plan dataset IDs differ from the comparison")
        if plan.code_revision != self.code_revision:
            raise ValueError("validation plan code revision differs from the comparison")
        return replace(self, validation_plan_sha256=plan.content_sha256())

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmark": {
                "benchmark_id": self.benchmark_id,
                "revision": self.benchmark_revision,
            },
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "code_revision": self.code_revision,
            "cost_scenario": {
                "cost_scenario_id": self.cost_scenario_id,
                "sha256": self.cost_scenario_sha256,
            },
            "created_at": self.created_at.isoformat(),
            "dataset_ids": list(self.dataset_ids),
            "exploration_report_sha256": self.exploration_report_sha256,
            "exploration_run_id": self.exploration_run_id,
            "report_id": self.report_id,
            "selection": {
                "reason": self.selection_reason,
                "selected_attempt_id": self.selected_attempt_id,
            },
            "validation_plan_sha256": self.validation_plan_sha256,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_markdown(self) -> str:
        rows = [
            "# Strategy Comparison Report",
            "",
            f"- Report ID: `{_markdown(self.report_id)}`",
            f"- Exploration run: `{_markdown(self.exploration_run_id)}`",
            f"- Dataset IDs: {', '.join(f'`{_markdown(item)}`' for item in self.dataset_ids)}",
            f"- Cost scenario: `{_markdown(self.cost_scenario_id)}`",
            f"- Benchmark: `{_markdown(self.benchmark_id)}` @ `{_markdown(self.benchmark_revision)}`",
            f"- Selected attempt: `{_markdown(self.selected_attempt_id)}`",
            f"- Selection reason: {_markdown(self.selection_reason)}",
            "",
            "## Candidate summary",
            "",
            "| Candidate | Economic hypothesis | Gross PnL | Total cost | Economic Net | Estimated tax | Estimated after-tax | OOS return | Turnover | Event concentration | Capacity limit | Tax confidence | Selected |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
        for candidate in self.candidates:
            capacity = (
                "not estimated"
                if candidate.capacity_limit_notional is None
                else str(candidate.capacity_limit_notional)
            )
            rows.append(
                "| "
                + " | ".join(
                    (
                        _markdown(candidate.candidate_id),
                        _markdown(candidate.economic_hypothesis),
                        str(candidate.gross_pnl),
                        str(candidate.total_cost),
                        str(candidate.economic_net_pnl),
                        str(candidate.tax.estimated_tax),
                        str(candidate.estimated_after_tax_pnl),
                        str(candidate.oos_return),
                        str(candidate.turnover),
                        str(candidate.event_concentration),
                        capacity,
                        candidate.tax.confidence.value,
                        "yes" if candidate.attempt_id == self.selected_attempt_id else "no",
                    )
                )
                + " |"
            )
        rows.extend(("", "## Cost waterfalls", ""))
        for candidate in self.candidates:
            rows.append(f"### {_markdown(candidate.candidate_id)}")
            rows.append("")
            rows.append(f"- Gross PnL: `{candidate.gross_pnl}`")
            if candidate.costs:
                for line in candidate.costs:
                    rows.append(
                        f"- {_markdown(line.label)} (`{_markdown(line.cost_id)}`): `{line.amount}`"
                    )
            else:
                rows.append("- Cost lines: none")
            rows.append(f"- Economic Net PnL: `{candidate.economic_net_pnl}`")
            rows.append(
                f"- Tax estimate (`{_markdown(candidate.tax.rule_version)}`, "
                f"{candidate.tax.confidence.value}): `{candidate.tax.estimated_tax}`"
            )
            rows.append(f"- Estimated after-tax PnL: `{candidate.estimated_after_tax_pnl}`")
            rows.append("")
        rows.extend(("## Exploration disclosure", ""))
        for candidate in self.candidates:
            rows.append(
                f"- **{_markdown(candidate.candidate_id)}**: "
                f"{candidate.exploration_trials} trial(s); "
                + "; ".join(
                    f"{_markdown(item.name)}=[{', '.join(_markdown(value) for value in item.values)}]"
                    for item in candidate.parameter_search
                )
            )
        rows.extend(("", "## Validation binding", ""))
        if self.validation_plan_sha256 is None:
            rows.append("No Validation plan has been sealed for this report.")
        else:
            rows.append(f"Validation plan SHA-256: `{self.validation_plan_sha256}`")
        return "\n".join(rows) + "\n"


def _require_parameters_disclosed(
    parameters: tuple[tuple[str, str], ...],
    search_spaces: tuple[ParameterSearchSpace, ...],
) -> None:
    disclosed = {item.name: item.values for item in search_spaces}
    for name, value in parameters:
        values = disclosed.get(name)
        if values is None or value not in values:
            raise ValueError("selected candidate parameters are missing from search disclosure")


def _markdown(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _nonnegative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be finite and non-negative")


def _positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= ZERO:
        raise ValueError(f"{name} must be finite and positive")


def _unit_interval(value: Decimal, name: str) -> None:
    if not value.is_finite() or not ZERO <= value <= ONE:
        raise ValueError(f"{name} must be between 0 and 1")


def _unique_text(values: tuple[str, ...], name: str, *, allow_empty: bool = False) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{name} must not be empty")
    for value in values:
        _text(value, name)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


def _sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in hexdigits for character in normalized):
        raise ValueError(f"{name} must be a 64-character hexadecimal digest")


def _git_sha(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) not in {40, 64} or any(
        character not in hexdigits for character in normalized
    ):
        raise ValueError(f"{name} must be a 40- or 64-character hexadecimal revision")
