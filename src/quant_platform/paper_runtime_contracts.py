"""Durable paper-runtime contracts, lease records, and smoke reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")


class PaperOperationKind(StrEnum):
    CYCLE = "CYCLE"
    RECONCILIATION = "RECONCILIATION"


class PaperOperationStatus(StrEnum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"


@dataclass(frozen=True, slots=True)
class PaperRuntimeLease:
    session_id: str
    owner_id: str
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("session_id", "owner_id"):
            _text(getattr(self, name), name)
        if self.fencing_token <= 0:
            raise ValueError("fencing_token must be positive")
        _aware(self.acquired_at, "acquired_at")
        _aware(self.expires_at, "expires_at")
        if self.expires_at <= self.acquired_at:
            raise ValueError("expires_at must follow acquired_at")


@dataclass(frozen=True, slots=True)
class PaperOperationRecord:
    sequence: int
    operation_id: str
    kind: PaperOperationKind
    status: PaperOperationStatus
    request_sha256: str
    result_sha256: str | None
    started_at: datetime
    committed_at: datetime | None

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        _text(self.operation_id, "operation_id")
        _sha256(self.request_sha256, "request_sha256")
        if self.result_sha256 is not None:
            _sha256(self.result_sha256, "result_sha256")
        _aware(self.started_at, "started_at")
        if self.committed_at is not None:
            _aware(self.committed_at, "committed_at")
            if self.committed_at < self.started_at:
                raise ValueError("committed_at must not precede started_at")
        if self.status is PaperOperationStatus.COMMITTED:
            if self.result_sha256 is None or self.committed_at is None:
                raise ValueError("committed operations require result digest and time")
        elif self.result_sha256 is not None:
            raise ValueError("non-committed operations must not define result digest")


@dataclass(frozen=True, slots=True)
class PaperRuntimeSnapshot:
    snapshot_id: str
    operation_sequence: int
    created_at: datetime
    session_sha256: str
    checkpoint_id: str
    event_sequence: int
    event_log_sha256: str
    state_sha256: str

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "checkpoint_id"):
            _text(getattr(self, name), name)
        if self.operation_sequence < 0:
            raise ValueError("operation_sequence must be non-negative")
        if self.event_sequence < 0:
            raise ValueError("event_sequence must be non-negative")
        _aware(self.created_at, "created_at")
        for name in ("session_sha256", "event_log_sha256", "state_sha256"):
            _sha256(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class DailyPaperSmokeReport:
    report_id: str
    session_id: str
    report_date: date
    high_water_operation_sequence: int
    cycle_count: int
    reconciliation_count: int
    reconciliation_mismatch_count: int
    aborted_operation_count: int
    pending_operation_count: int
    status_counts: tuple[tuple[str, int], ...]
    executed_quantity: Decimal
    fee_amount: Decimal
    first_event_sequence: int | None
    last_event_sequence: int | None
    latest_session_sha256: str
    journal_head_sha256: str
    healthy: bool
    findings: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("report_id", "session_id"):
            _text(getattr(self, name), name)
        for name in (
            "high_water_operation_sequence",
            "cycle_count",
            "reconciliation_count",
            "reconciliation_mismatch_count",
            "aborted_operation_count",
            "pending_operation_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        _nonnegative(self.executed_quantity, "executed_quantity")
        _nonnegative(self.fee_amount, "fee_amount")
        for value, name in (
            (self.first_event_sequence, "first_event_sequence"),
            (self.last_event_sequence, "last_event_sequence"),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        _sha256(self.latest_session_sha256, "latest_session_sha256")
        _sha256(self.journal_head_sha256, "journal_head_sha256")
        names = [name for name, _ in self.status_counts]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("status_counts must be unique and sorted")
        if any(count < 0 for _, count in self.status_counts):
            raise ValueError("status counts must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "aborted_operation_count": self.aborted_operation_count,
            "cycle_count": self.cycle_count,
            "executed_quantity": str(self.executed_quantity),
            "fee_amount": str(self.fee_amount),
            "findings": list(self.findings),
            "first_event_sequence": self.first_event_sequence,
            "healthy": self.healthy,
            "high_water_operation_sequence": self.high_water_operation_sequence,
            "journal_head_sha256": self.journal_head_sha256,
            "last_event_sequence": self.last_event_sequence,
            "latest_session_sha256": self.latest_session_sha256,
            "pending_operation_count": self.pending_operation_count,
            "reconciliation_count": self.reconciliation_count,
            "reconciliation_mismatch_count": self.reconciliation_mismatch_count,
            "report_date": self.report_date.isoformat(),
            "report_id": self.report_id,
            "session_id": self.session_id,
            "status_counts": {name: count for name, count in self.status_counts},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def content_sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_markdown(self) -> str:
        lines = [
            f"# Daily Paper Smoke Report — {self.report_date.isoformat()}",
            "",
            f"- Session: `{self.session_id}`",
            f"- Report: `{self.report_id}`",
            f"- Healthy: **{'YES' if self.healthy else 'NO'}**",
            f"- High-water operation: `{self.high_water_operation_sequence}`",
            f"- Session SHA-256: `{self.latest_session_sha256}`",
            f"- Journal head: `{self.journal_head_sha256}`",
            "",
            "## Activity",
            "",
            f"- Cycles: {self.cycle_count}",
            f"- Reconciliations: {self.reconciliation_count}",
            f"- Reconciliation mismatches: {self.reconciliation_mismatch_count}",
            f"- Pending operations: {self.pending_operation_count}",
            f"- Aborted operations: {self.aborted_operation_count}",
            f"- Executed quantity: {self.executed_quantity}",
            f"- Fees: {self.fee_amount}",
            "",
            "## Cycle statuses",
            "",
        ]
        if self.status_counts:
            lines.extend(f"- {name}: {count}" for name, count in self.status_counts)
        else:
            lines.append("No cycles recorded for this date.")
        lines.extend(["", "## Findings", ""])
        if self.findings:
            lines.extend(f"- {finding}" for finding in self.findings)
        else:
            lines.append("No blocking runtime findings.")
        return "\n".join(lines) + "\n"


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")


def _nonnegative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be finite and non-negative")


__all__ = [
    "DailyPaperSmokeReport",
    "PaperOperationKind",
    "PaperOperationRecord",
    "PaperOperationStatus",
    "PaperRuntimeLease",
    "PaperRuntimeSnapshot",
]
