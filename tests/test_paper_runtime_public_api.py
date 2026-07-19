from datetime import date
from decimal import Decimal

from quant_platform.paper import (
    DailyPaperSmokeReport,
    DurablePaperRuntime,
    PaperIdempotencyConflictError,
    PaperLeaseConflictError,
    PaperOperationKind,
    PaperOperationStatus,
    PaperRecoveryIntegrityError,
    PaperRuntimeError,
    PaperRuntimeLease,
    PaperRuntimeSnapshot,
)


def test_durable_runtime_public_surface_and_report_serialization() -> None:
    assert DurablePaperRuntime.__name__ == "DurablePaperRuntime"
    assert issubclass(PaperLeaseConflictError, PaperRuntimeError)
    assert issubclass(PaperIdempotencyConflictError, PaperRuntimeError)
    assert issubclass(PaperRecoveryIntegrityError, PaperRuntimeError)
    assert PaperOperationKind.CYCLE.value == "CYCLE"
    assert PaperOperationStatus.PENDING.value == "PENDING"
    assert PaperRuntimeLease.__name__ == "PaperRuntimeLease"
    assert PaperRuntimeSnapshot.__name__ == "PaperRuntimeSnapshot"

    report = DailyPaperSmokeReport(
        report_id="paper-session:2026-01-01:op-0",
        session_id="paper-session",
        report_date=date(2026, 1, 1),
        high_water_operation_sequence=0,
        cycle_count=0,
        reconciliation_count=0,
        reconciliation_mismatch_count=0,
        aborted_operation_count=0,
        pending_operation_count=0,
        status_counts=(),
        executed_quantity=Decimal("0"),
        fee_amount=Decimal("0"),
        first_event_sequence=None,
        last_event_sequence=None,
        latest_session_sha256="a" * 64,
        journal_head_sha256="b" * 64,
        healthy=True,
        findings=(),
    )

    assert report.to_json() == report.to_json()
    assert "Healthy: **YES**" in report.to_markdown()
    assert len(report.content_sha256()) == 64
