"""Immutable strategy decision packages and lifecycle promotion gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from string import hexdigits
from typing import Any


class StrategyLifecycleState(StrEnum):
    IDEA = "IDEA"
    IMPLEMENTED = "IMPLEMENTED"
    DEVELOPMENT_VALIDATED = "DEVELOPMENT_VALIDATED"
    HOLDOUT_VALIDATED = "HOLDOUT_VALIDATED"
    PAPER = "PAPER"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    LIVE = "LIVE"
    RETIRED = "RETIRED"


class StrategyDecisionOutcome(StrEnum):
    APPROVED = "APPROVED"
    HELD = "HELD"
    REJECTED = "REJECTED"


class DecisionReasonSeverity(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class DecisionReasonCode(StrEnum):
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    DATA_QUALITY = "DATA_QUALITY"
    LOOKAHEAD_RISK = "LOOKAHEAD_RISK"
    HOLDOUT_LEAKAGE = "HOLDOUT_LEAKAGE"
    ECONOMICALLY_UNVIABLE = "ECONOMICALLY_UNVIABLE"
    COST_SENSITIVITY = "COST_SENSITIVITY"
    TAX_UNRESOLVED = "TAX_UNRESOLVED"
    PAPER_RECONCILIATION = "PAPER_RECONCILIATION"
    RISK_LIMIT = "RISK_LIMIT"
    OPERATIONAL_READINESS = "OPERATIONAL_READINESS"
    MANUAL_DECISION = "MANUAL_DECISION"
    OTHER = "OTHER"


class RevisionKind(StrEnum):
    DATASET = "DATASET"
    CODE = "CODE"
    CONFIG = "CONFIG"
    RULE = "RULE"
    ENVIRONMENT = "ENVIRONMENT"
    REPORT = "REPORT"
    ARTIFACT = "ARTIFACT"


class DigestAlgorithm(StrEnum):
    GIT_SHA1 = "GIT_SHA1"
    SHA256 = "SHA256"


class PromotionGate(StrEnum):
    IMPLEMENTATION = "IMPLEMENTATION"
    DEVELOPMENT_VALIDATION = "DEVELOPMENT_VALIDATION"
    HOLDOUT_VALIDATION = "HOLDOUT_VALIDATION"
    PAPER_READINESS = "PAPER_READINESS"
    PAPER_RECONCILIATION = "PAPER_RECONCILIATION"
    LIVE_READINESS = "LIVE_READINESS"
    RETIREMENT = "RETIREMENT"


_REQUIRED_GATE: dict[tuple[StrategyLifecycleState, StrategyLifecycleState], PromotionGate] = {
    (StrategyLifecycleState.IDEA, StrategyLifecycleState.IMPLEMENTED): PromotionGate.IMPLEMENTATION,
    (
        StrategyLifecycleState.IMPLEMENTED,
        StrategyLifecycleState.DEVELOPMENT_VALIDATED,
    ): PromotionGate.DEVELOPMENT_VALIDATION,
    (
        StrategyLifecycleState.DEVELOPMENT_VALIDATED,
        StrategyLifecycleState.HOLDOUT_VALIDATED,
    ): PromotionGate.HOLDOUT_VALIDATION,
    (
        StrategyLifecycleState.HOLDOUT_VALIDATED,
        StrategyLifecycleState.PAPER,
    ): PromotionGate.PAPER_READINESS,
    (
        StrategyLifecycleState.PAPER,
        StrategyLifecycleState.LIVE_CANDIDATE,
    ): PromotionGate.PAPER_RECONCILIATION,
    (
        StrategyLifecycleState.LIVE_CANDIDATE,
        StrategyLifecycleState.LIVE,
    ): PromotionGate.LIVE_READINESS,
}

_STATE_RANK: dict[StrategyLifecycleState, int] = {
    StrategyLifecycleState.IDEA: 0,
    StrategyLifecycleState.IMPLEMENTED: 1,
    StrategyLifecycleState.DEVELOPMENT_VALIDATED: 2,
    StrategyLifecycleState.HOLDOUT_VALIDATED: 3,
    StrategyLifecycleState.PAPER: 4,
    StrategyLifecycleState.LIVE_CANDIDATE: 5,
    StrategyLifecycleState.LIVE: 6,
    StrategyLifecycleState.RETIRED: 7,
}


@dataclass(frozen=True, slots=True)
class RevisionEvidence:
    evidence_id: str
    kind: RevisionKind
    name: str
    reference: str
    revision: str
    digest_algorithm: DigestAlgorithm
    digest: str

    def __post_init__(self) -> None:
        for field_name in ("evidence_id", "name", "reference", "revision"):
            _require_text(getattr(self, field_name), field_name)
        normalized = self.digest.lower()
        expected_length = 40 if self.digest_algorithm is DigestAlgorithm.GIT_SHA1 else 64
        if len(normalized) != expected_length or any(
            character not in hexdigits for character in normalized
        ):
            raise ValueError(
                f"digest must be a {expected_length}-character hexadecimal value for "
                f"{self.digest_algorithm.value}"
            )
        if self.digest_algorithm is DigestAlgorithm.GIT_SHA1 and self.revision != normalized:
            raise ValueError("GIT_SHA1 revision must equal digest")
        object.__setattr__(self, "digest", normalized)


@dataclass(frozen=True, slots=True)
class HoldoutSeal:
    seal_id: str
    dataset_evidence_id: str
    development_end: date
    holdout_start: date
    holdout_end: date
    split_spec_sha256: str
    acceptance_criteria_sha256: str
    sealed_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("seal_id", "dataset_evidence_id"):
            _require_text(getattr(self, field_name), field_name)
        if self.development_end >= self.holdout_start:
            raise ValueError("development_end must precede holdout_start")
        if self.holdout_start > self.holdout_end:
            raise ValueError("holdout_start must not follow holdout_end")
        _require_sha256(self.split_spec_sha256, "split_spec_sha256")
        _require_sha256(self.acceptance_criteria_sha256, "acceptance_criteria_sha256")
        _require_aware(self.sealed_at, "sealed_at")
        object.__setattr__(self, "split_spec_sha256", self.split_spec_sha256.lower())
        object.__setattr__(
            self,
            "acceptance_criteria_sha256",
            self.acceptance_criteria_sha256.lower(),
        )


@dataclass(frozen=True, slots=True)
class GateResult:
    result_id: str
    gate: PromotionGate
    passed: bool
    evaluated_at: datetime
    evaluator: str
    criteria_sha256: str
    evidence_ids: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        for field_name in ("result_id", "evaluator", "summary"):
            _require_text(getattr(self, field_name), field_name)
        _require_aware(self.evaluated_at, "evaluated_at")
        _require_sha256(self.criteria_sha256, "criteria_sha256")
        if not self.evidence_ids:
            raise ValueError("evidence_ids must not be empty")
        _require_unique_text(self.evidence_ids, "evidence_ids")
        object.__setattr__(self, "criteria_sha256", self.criteria_sha256.lower())


@dataclass(frozen=True, slots=True)
class DecisionReason:
    reason_id: str
    code: DecisionReasonCode
    severity: DecisionReasonSeverity
    detail: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("reason_id", "detail"):
            _require_text(getattr(self, field_name), field_name)
        _require_unique_text(self.evidence_ids, "evidence_ids", allow_empty=True)


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    decision_id: str
    from_state: StrategyLifecycleState
    target_state: StrategyLifecycleState
    outcome: StrategyDecisionOutcome
    decided_at: datetime
    decided_by: str
    gate_result_ids: tuple[str, ...]
    reasons: tuple[DecisionReason, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        for field_name in ("decision_id", "decided_by"):
            _require_text(getattr(self, field_name), field_name)
        _require_aware(self.decided_at, "decided_at")
        _require_unique_text(self.gate_result_ids, "gate_result_ids", allow_empty=True)
        reason_ids = tuple(reason.reason_id for reason in self.reasons)
        _require_unique_text(reason_ids, "reason IDs", allow_empty=True)
        if self.outcome is StrategyDecisionOutcome.APPROVED:
            if any(reason.severity is DecisionReasonSeverity.BLOCKING for reason in self.reasons):
                raise ValueError("approved decisions must not contain blocking reasons")
        elif not any(
            reason.severity is DecisionReasonSeverity.BLOCKING for reason in self.reasons
        ):
            raise ValueError("held and rejected decisions require a blocking reason")


@dataclass(frozen=True, slots=True)
class StrategyDecisionPackage:
    package_id: str
    strategy_id: str
    package_version: str
    hypothesis: str
    state: StrategyLifecycleState
    created_at: datetime
    updated_at: datetime
    revisions: tuple[RevisionEvidence, ...]
    gate_results: tuple[GateResult, ...] = ()
    decisions: tuple[ApprovalRecord, ...] = ()
    holdout_seal: HoldoutSeal | None = None
    known_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("package_id", "strategy_id", "package_version", "hypothesis"):
            _require_text(getattr(self, field_name), field_name)
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        _require_unique_text(
            tuple(revision.evidence_id for revision in self.revisions),
            "revision evidence IDs",
            allow_empty=True,
        )
        _require_unique_text(
            tuple(result.result_id for result in self.gate_results),
            "gate result IDs",
            allow_empty=True,
        )
        _require_unique_text(
            tuple(decision.decision_id for decision in self.decisions),
            "decision IDs",
            allow_empty=True,
        )
        _require_unique_text(self.known_limitations, "known_limitations", allow_empty=True)
        self._validate_references()
        self._validate_chronology()
        self._validate_state_evidence()

    def _validate_references(self) -> None:
        evidence_ids = {revision.evidence_id for revision in self.revisions}
        gate_ids = {result.result_id for result in self.gate_results}
        for result in self.gate_results:
            missing = set(result.evidence_ids) - evidence_ids
            if missing:
                raise ValueError(f"gate result references missing evidence: {sorted(missing)}")
            if result.gate is PromotionGate.HOLDOUT_VALIDATION:
                if self.holdout_seal is None:
                    raise ValueError("holdout validation requires a holdout seal")
                if result.evaluated_at < self.holdout_seal.sealed_at:
                    raise ValueError("holdout validation must occur after the holdout seal")
                if result.criteria_sha256 != self.holdout_seal.acceptance_criteria_sha256:
                    raise ValueError(
                        "holdout validation criteria must match the sealed acceptance criteria"
                    )
        if self.holdout_seal is not None:
            dataset = next(
                (
                    revision
                    for revision in self.revisions
                    if revision.evidence_id == self.holdout_seal.dataset_evidence_id
                ),
                None,
            )
            if dataset is None:
                raise ValueError("holdout seal references missing dataset evidence")
            if dataset.kind is not RevisionKind.DATASET:
                raise ValueError("holdout seal must reference DATASET evidence")
        for decision in self.decisions:
            missing_gates = set(decision.gate_result_ids) - gate_ids
            if missing_gates:
                raise ValueError(
                    f"decision references missing gate results: {sorted(missing_gates)}"
                )
            referenced_gates = tuple(
                result for result in self.gate_results if result.result_id in decision.gate_result_ids
            )
            if any(result.evaluated_at > decision.decided_at for result in referenced_gates):
                raise ValueError("decisions must not precede their referenced gate results")
            for reason in decision.reasons:
                missing_evidence = set(reason.evidence_ids) - evidence_ids
                if missing_evidence:
                    raise ValueError(
                        f"decision reason references missing evidence: {sorted(missing_evidence)}"
                    )

    def _validate_chronology(self) -> None:
        gate_times = [result.evaluated_at for result in self.gate_results]
        if gate_times != sorted(gate_times):
            raise ValueError("gate results must be chronological")
        decision_times = [decision.decided_at for decision in self.decisions]
        if decision_times != sorted(decision_times):
            raise ValueError("decisions must be chronological")
        if any(result.evaluated_at > self.updated_at for result in self.gate_results):
            raise ValueError("gate results must not occur after updated_at")
        if any(decision.decided_at > self.updated_at for decision in self.decisions):
            raise ValueError("decisions must not occur after updated_at")

    def _validate_state_evidence(self) -> None:
        if (
            self.state is not StrategyLifecycleState.RETIRED
            and _STATE_RANK[self.state]
            >= _STATE_RANK[StrategyLifecycleState.DEVELOPMENT_VALIDATED]
        ):
            kinds = {revision.kind for revision in self.revisions}
            required = {RevisionKind.DATASET, RevisionKind.CODE, RevisionKind.RULE}
            missing = required - kinds
            if missing:
                labels = ", ".join(sorted(kind.value for kind in missing))
                raise ValueError(
                    "development-validated packages require revision evidence for: " + labels
                )
        if (
            self.state is not StrategyLifecycleState.RETIRED
            and _STATE_RANK[self.state] >= _STATE_RANK[StrategyLifecycleState.HOLDOUT_VALIDATED]
        ):
            if self.holdout_seal is None:
                raise ValueError("holdout-validated and later packages require a holdout seal")
        if self.state is StrategyLifecycleState.RETIRED and not self.decisions:
            raise ValueError("retired packages require a retirement decision")

    def add_gate_result(self, result: GateResult, *, updated_at: datetime) -> StrategyDecisionPackage:
        _require_aware(updated_at, "updated_at")
        if updated_at < self.updated_at or updated_at < result.evaluated_at:
            raise ValueError("updated_at must include the new gate result")
        if any(existing.result_id == result.result_id for existing in self.gate_results):
            raise ValueError("gate result ID already exists")
        if result.gate is PromotionGate.HOLDOUT_VALIDATION:
            if self.holdout_seal is None:
                raise ValueError("holdout validation requires a holdout seal")
            if result.criteria_sha256 != self.holdout_seal.acceptance_criteria_sha256:
                raise ValueError(
                    "holdout validation criteria must match the sealed acceptance criteria"
                )
        return replace(
            self,
            gate_results=self.gate_results + (result,),
            updated_at=updated_at,
        )

    def seal_holdout(
        self,
        seal: HoldoutSeal,
        *,
        updated_at: datetime,
    ) -> StrategyDecisionPackage:
        _require_aware(updated_at, "updated_at")
        if self.holdout_seal is not None:
            raise ValueError("holdout policy is already sealed and cannot be replaced")
        if _STATE_RANK[self.state] > _STATE_RANK[StrategyLifecycleState.DEVELOPMENT_VALIDATED]:
            raise ValueError("holdout policy must be sealed before holdout validation")
        if updated_at < self.updated_at or updated_at < seal.sealed_at:
            raise ValueError("updated_at must include the holdout seal")
        return replace(self, holdout_seal=seal, updated_at=updated_at)

    def apply_decision(
        self,
        decision: ApprovalRecord,
        *,
        updated_at: datetime,
    ) -> StrategyDecisionPackage:
        _require_aware(updated_at, "updated_at")
        if self.state is StrategyLifecycleState.RETIRED:
            raise ValueError("RETIRED is terminal")
        if decision.from_state is not self.state:
            raise ValueError("decision from_state must match the package state")
        required_gate = required_promotion_gate(self.state, decision.target_state)
        gate_by_id = {result.result_id: result for result in self.gate_results}
        missing_gate_ids = set(decision.gate_result_ids) - set(gate_by_id)
        if missing_gate_ids:
            raise ValueError(
                f"decision references missing gate results: {sorted(missing_gate_ids)}"
            )
        referenced = tuple(gate_by_id[result_id] for result_id in decision.gate_result_ids)
        if any(result.evaluated_at > decision.decided_at for result in referenced):
            raise ValueError("decision must not precede its referenced gate results")
        if decision.outcome is StrategyDecisionOutcome.APPROVED:
            matching = tuple(result for result in referenced if result.gate is required_gate)
            if not matching:
                raise ValueError(
                    f"approved transition requires a referenced {required_gate.value} gate result"
                )
            if not all(result.passed for result in matching):
                raise ValueError("approved transition requires a passing gate result")
            next_state = decision.target_state
        else:
            next_state = self.state
        if updated_at < self.updated_at or updated_at < decision.decided_at:
            raise ValueError("updated_at must include the decision")
        return replace(
            self,
            state=next_state,
            decisions=self.decisions + (decision,),
            updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "strategy_id": self.strategy_id,
            "package_version": self.package_version,
            "hypothesis": self.hypothesis,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "revisions": [
                {
                    "evidence_id": revision.evidence_id,
                    "kind": revision.kind.value,
                    "name": revision.name,
                    "reference": revision.reference,
                    "revision": revision.revision,
                    "digest_algorithm": revision.digest_algorithm.value,
                    "digest": revision.digest,
                }
                for revision in self.revisions
            ],
            "holdout_seal": _holdout_to_dict(self.holdout_seal),
            "gate_results": [
                {
                    "result_id": result.result_id,
                    "gate": result.gate.value,
                    "passed": result.passed,
                    "evaluated_at": result.evaluated_at.isoformat(),
                    "evaluator": result.evaluator,
                    "criteria_sha256": result.criteria_sha256,
                    "evidence_ids": list(result.evidence_ids),
                    "summary": result.summary,
                }
                for result in self.gate_results
            ],
            "decisions": [
                {
                    "decision_id": decision.decision_id,
                    "from_state": decision.from_state.value,
                    "target_state": decision.target_state.value,
                    "outcome": decision.outcome.value,
                    "decided_at": decision.decided_at.isoformat(),
                    "decided_by": decision.decided_by,
                    "gate_result_ids": list(decision.gate_result_ids),
                    "reasons": [
                        {
                            "reason_id": reason.reason_id,
                            "code": reason.code.value,
                            "severity": reason.severity.value,
                            "detail": reason.detail,
                            "evidence_ids": list(reason.evidence_ids),
                        }
                        for reason in decision.reasons
                    ],
                    "rationale": decision.rationale,
                }
                for decision in self.decisions
            ],
            "known_limitations": list(self.known_limitations),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_markdown(self) -> str:
        lines = [
            f"# Strategy Decision Package — {self.strategy_id}",
            "",
            f"- Package: `{self.package_id}` / `{self.package_version}`",
            f"- State: **{self.state.value}**",
            f"- Updated: {self.updated_at.isoformat()}",
            f"- JSON SHA-256: `{self.content_sha256()}`",
            "",
            "## Hypothesis",
            "",
            self.hypothesis,
            "",
            "## Immutable revisions",
            "",
        ]
        for revision in self.revisions:
            lines.append(
                f"- `{revision.kind.value}` **{revision.name}** — `{revision.revision}` "
                f"({revision.digest_algorithm.value}: `{revision.digest}`)"
            )
        lines.extend(["", "## Holdout seal", ""])
        if self.holdout_seal is None:
            lines.append("Not sealed.")
        else:
            lines.extend(
                [
                    f"- Seal: `{self.holdout_seal.seal_id}`",
                    f"- Development end: {self.holdout_seal.development_end.isoformat()}",
                    f"- Holdout: {self.holdout_seal.holdout_start.isoformat()} "
                    f"to {self.holdout_seal.holdout_end.isoformat()}",
                    f"- Split spec: `{self.holdout_seal.split_spec_sha256}`",
                    f"- Acceptance criteria: "
                    f"`{self.holdout_seal.acceptance_criteria_sha256}`",
                ]
            )
        lines.extend(["", "## Gate results", ""])
        for result in self.gate_results:
            mark = "PASS" if result.passed else "FAIL"
            lines.append(
                f"- **{result.gate.value} — {mark}** (`{result.result_id}`): {result.summary}"
            )
        if not self.gate_results:
            lines.append("No gate results recorded.")
        lines.extend(["", "## Decisions", ""])
        for decision in self.decisions:
            lines.append(
                f"- **{decision.outcome.value}** {decision.from_state.value} → "
                f"{decision.target_state.value} by {decision.decided_by} "
                f"at {decision.decided_at.isoformat()}"
            )
            for reason in decision.reasons:
                lines.append(
                    f"  - {reason.severity.value} / {reason.code.value}: {reason.detail}"
                )
        if not self.decisions:
            lines.append("No decisions recorded.")
        lines.extend(["", "## Known limitations", ""])
        if self.known_limitations:
            lines.extend(f"- {limitation}" for limitation in self.known_limitations)
        else:
            lines.append("None recorded.")
        return "\n".join(lines) + "\n"


def required_promotion_gate(
    from_state: StrategyLifecycleState,
    target_state: StrategyLifecycleState,
) -> PromotionGate:
    if target_state is StrategyLifecycleState.RETIRED:
        return PromotionGate.RETIREMENT
    gate = _REQUIRED_GATE.get((from_state, target_state))
    if gate is None:
        raise ValueError(f"illegal lifecycle transition: {from_state.value} -> {target_state.value}")
    return gate


def allowed_target_states(
    state: StrategyLifecycleState,
) -> tuple[StrategyLifecycleState, ...]:
    """Return legal approved targets, including explicit retirement."""

    if state is StrategyLifecycleState.RETIRED:
        return ()
    direct = tuple(target for source, target in _REQUIRED_GATE if source is state)
    return direct + (StrategyLifecycleState.RETIRED,)


def _holdout_to_dict(seal: HoldoutSeal | None) -> dict[str, str] | None:
    if seal is None:
        return None
    return {
        "seal_id": seal.seal_id,
        "dataset_evidence_id": seal.dataset_evidence_id,
        "development_end": seal.development_end.isoformat(),
        "holdout_start": seal.holdout_start.isoformat(),
        "holdout_end": seal.holdout_end.isoformat(),
        "split_spec_sha256": seal.split_spec_sha256,
        "acceptance_criteria_sha256": seal.acceptance_criteria_sha256,
        "sealed_at": seal.sealed_at.isoformat(),
    }


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in hexdigits for character in normalized):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256 digest")


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
