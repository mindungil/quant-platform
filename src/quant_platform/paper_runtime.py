"""Crash-consistent durable runtime for deterministic paper sessions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from .contracts import AlphaPlugin, MarketBar
from .execution_profiles import ExecutionProfileSnapshot
from .paper_contracts import PaperCycleRequest, PaperCycleResult, PaperLaunchAuthorization
from .paper_orchestrator import PaperTradingOrchestrator
from .paper_runtime_contracts import (
    DailyPaperSmokeReport,
    PaperOperationKind,
    PaperOperationRecord,
    PaperOperationStatus,
    PaperRuntimeLease,
    PaperRuntimeSnapshot,
)
from .risk_engine import BrokerSnapshot, ReconciliationResult, SingleStrategyRiskPolicy
from .strategy_decision import StrategyDecisionPackage
from .venue_simulator import VenueQuote, VenueSimulationConfig

GENESIS_SHA256 = "0" * 64
SCHEMA_VERSION = "durable-paper-runtime-v1"


class PaperRuntimeError(RuntimeError):
    """Base durable-runtime failure."""


class PaperLeaseConflictError(PaperRuntimeError):
    """Raised when a stale or competing writer attempts to mutate a session."""


class PaperIdempotencyConflictError(PaperRuntimeError):
    """Raised when one operation ID is reused with a different request."""


class PaperRecoveryIntegrityError(PaperRuntimeError):
    """Raised when persisted evidence cannot be reproduced exactly."""


class DurablePaperRuntime:
    """Persist, fence, recover, and audit one deterministic paper session.

    SQLite is used as a reference durable store with WAL and FULL synchronous
    durability. Commands are staged before execution. A command is evaluated on
    a freshly replayed orchestrator and its result plus complete session snapshot
    are committed in one database transaction. This prevents a failed command
    from half-mutating the active in-memory session.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        plugin: AlphaPlugin,
        decision_package: StrategyDecisionPackage,
        authorization: PaperLaunchAuthorization,
        venue_profile: ExecutionProfileSnapshot,
        risk_policy: SingleStrategyRiskPolicy,
        venue_config: VenueSimulationConfig | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.plugin = plugin
        self.decision_package = decision_package
        self.authorization = authorization
        self.venue_profile = venue_profile
        self.risk_policy = risk_policy
        self.venue_config = venue_config or VenueSimulationConfig()
        self._connection = sqlite3.connect(self.database_path, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._configure_database()
        self._create_schema()
        if self._metadata_value("schema_version") is None:
            self._initialize_store()
        else:
            self._validate_store_identity()
        self._verify_journal_chain()
        (
            self._orchestrator,
            self._cycle_results,
            self._reconciliation_results,
        ) = self._rebuild_committed(verify_snapshots=True)

    @property
    def orchestrator(self) -> PaperTradingOrchestrator:
        return self._orchestrator

    @property
    def operations(self) -> tuple[PaperOperationRecord, ...]:
        rows = self._connection.execute("SELECT * FROM operations ORDER BY sequence").fetchall()
        return tuple(_operation_record(row) for row in rows)

    @property
    def pending_operations(self) -> tuple[PaperOperationRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM operations WHERE status = ? ORDER BY sequence",
            (PaperOperationStatus.PENDING.value,),
        ).fetchall()
        return tuple(_operation_record(row) for row in rows)

    @property
    def latest_snapshot(self) -> PaperRuntimeSnapshot:
        row = self._connection.execute(
            "SELECT * FROM snapshots ORDER BY operation_sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise PaperRecoveryIntegrityError("durable paper runtime has no snapshot")
        return _snapshot_record(row)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> DurablePaperRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def acquire_lease(
        self,
        *,
        owner_id: str,
        now: datetime,
        ttl: timedelta,
    ) -> PaperRuntimeLease:
        _text(owner_id, "owner_id")
        _aware(now, "now")
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        expires_at = now + ttl
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM leases WHERE session_id = ?",
                (self.authorization.session_id,),
            ).fetchone()
            if row is None:
                token = 1
                event_type = "LEASE_ACQUIRED"
            else:
                current_expiry = _datetime(row["expires_at"])
                if row["owner_id"] != owner_id and current_expiry > now:
                    detail = (
                        f"paper session lease is held by {row['owner_id']} until "
                        f"{current_expiry.isoformat()}"
                    )
                    raise PaperLeaseConflictError(detail)
                if row["owner_id"] == owner_id and current_expiry > now:
                    token = int(row["fencing_token"])
                    event_type = "LEASE_RENEWED"
                else:
                    token = int(row["fencing_token"]) + 1
                    event_type = "LEASE_TAKEN_OVER"
            connection.execute(
                """
                INSERT INTO leases(session_id, owner_id, fencing_token, acquired_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    fencing_token = excluded.fencing_token,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at
                """,
                (
                    self.authorization.session_id,
                    owner_id,
                    token,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._append_journal(
                connection,
                event_id=(
                    f"lease-{self.authorization.session_id}-{token}-{event_type.lower()}-"
                    f"{_time_key(expires_at)}"
                ),
                event_type=event_type,
                occurred_at=now,
                payload={
                    "expires_at": expires_at.isoformat(),
                    "fencing_token": token,
                    "owner_id": owner_id,
                    "session_id": self.authorization.session_id,
                },
            )
        return PaperRuntimeLease(
            session_id=self.authorization.session_id,
            owner_id=owner_id,
            fencing_token=token,
            acquired_at=now,
            expires_at=expires_at,
        )

    def renew_lease(
        self,
        lease: PaperRuntimeLease,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> PaperRuntimeLease:
        _aware(now, "now")
        if ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        expires_at = now + ttl
        with self._transaction() as connection:
            self._require_lease(connection, lease, now)
            connection.execute(
                "UPDATE leases SET expires_at = ? WHERE session_id = ?",
                (expires_at.isoformat(), self.authorization.session_id),
            )
            self._append_journal(
                connection,
                event_id=(
                    f"lease-{lease.session_id}-{lease.fencing_token}-renew-"
                    f"{_time_key(expires_at)}"
                ),
                event_type="LEASE_RENEWED",
                occurred_at=now,
                payload={
                    "expires_at": expires_at.isoformat(),
                    "fencing_token": lease.fencing_token,
                    "owner_id": lease.owner_id,
                    "session_id": lease.session_id,
                },
            )
        return PaperRuntimeLease(
            session_id=lease.session_id,
            owner_id=lease.owner_id,
            fencing_token=lease.fencing_token,
            acquired_at=lease.acquired_at,
            expires_at=expires_at,
        )

    def release_lease(self, lease: PaperRuntimeLease, *, now: datetime) -> None:
        _aware(now, "now")
        with self._transaction() as connection:
            self._require_lease(connection, lease, now)
            connection.execute(
                "DELETE FROM leases WHERE session_id = ?",
                (self.authorization.session_id,),
            )
            self._append_journal(
                connection,
                event_id=(
                    f"lease-{lease.session_id}-{lease.fencing_token}-release-"
                    f"{_time_key(now)}"
                ),
                event_type="LEASE_RELEASED",
                occurred_at=now,
                payload={
                    "fencing_token": lease.fencing_token,
                    "owner_id": lease.owner_id,
                    "session_id": lease.session_id,
                },
            )

    def stage_cycle(
        self,
        request: PaperCycleRequest,
        *,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> PaperOperationRecord:
        return self._stage_operation(
            operation_id=request.cycle_id,
            kind=PaperOperationKind.CYCLE,
            request_json=_cycle_request_json(request),
            lease=lease,
            now=now,
        )

    def run_cycle(
        self,
        request: PaperCycleRequest,
        *,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> PaperCycleResult:
        record = self.stage_cycle(request, lease=lease, now=now)
        if record.status is PaperOperationStatus.COMMITTED:
            return self._cycle_results[record.operation_id]
        if record.status is PaperOperationStatus.ABORTED:
            raise PaperRuntimeError(f"paper operation is aborted: {record.operation_id}")
        result = self._commit_pending(record.operation_id, lease=lease, now=now)
        if not isinstance(result, PaperCycleResult):
            raise PaperRecoveryIntegrityError("cycle operation produced a reconciliation result")
        return result

    def stage_reconciliation(
        self,
        *,
        operation_id: str,
        decision_id: str,
        occurred_at: datetime,
        broker_snapshot: BrokerSnapshot,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> PaperOperationRecord:
        request_json = _reconciliation_request_json(
            decision_id=decision_id,
            occurred_at=occurred_at,
            broker_snapshot=broker_snapshot,
        )
        return self._stage_operation(
            operation_id=operation_id,
            kind=PaperOperationKind.RECONCILIATION,
            request_json=request_json,
            lease=lease,
            now=now,
        )

    def reconcile(
        self,
        *,
        operation_id: str,
        decision_id: str,
        occurred_at: datetime,
        broker_snapshot: BrokerSnapshot,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> ReconciliationResult:
        record = self.stage_reconciliation(
            operation_id=operation_id,
            decision_id=decision_id,
            occurred_at=occurred_at,
            broker_snapshot=broker_snapshot,
            lease=lease,
            now=now,
        )
        if record.status is PaperOperationStatus.COMMITTED:
            return self._reconciliation_results[record.operation_id]
        if record.status is PaperOperationStatus.ABORTED:
            raise PaperRuntimeError(f"paper operation is aborted: {record.operation_id}")
        result = self._commit_pending(record.operation_id, lease=lease, now=now)
        if not isinstance(result, ReconciliationResult):
            raise PaperRecoveryIntegrityError("reconciliation operation produced a cycle result")
        return result

    def recover_pending(
        self,
        *,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> tuple[PaperCycleResult | ReconciliationResult, ...]:
        _aware(now, "now")
        recovered: list[PaperCycleResult | ReconciliationResult] = []
        pending_ids = [operation.operation_id for operation in self.pending_operations]
        for operation_id in pending_ids:
            recovered.append(self._commit_pending(operation_id, lease=lease, now=now))
        if recovered:
            with self._transaction() as connection:
                self._require_lease(connection, lease, now)
                self._append_journal(
                    connection,
                    event_id=(
                        f"recovery-{self.authorization.session_id}-{lease.fencing_token}-"
                        f"{_time_key(now)}"
                    ),
                    event_type="RECOVERY_COMPLETED",
                    occurred_at=now,
                    payload={
                        "operation_ids": pending_ids,
                        "session_sha256": _sha256(self._orchestrator.session_json()),
                    },
                )
        return tuple(recovered)

    def abort_pending(
        self,
        operation_id: str,
        *,
        reason: str,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> PaperOperationRecord:
        _text(reason, "reason")
        _aware(now, "now")
        with self._transaction() as connection:
            self._require_lease(connection, lease, now)
            row = self._operation_row(connection, operation_id)
            if PaperOperationStatus(row["status"]) is not PaperOperationStatus.PENDING:
                raise PaperRuntimeError("only pending operations can be aborted")
            connection.execute(
                "UPDATE operations SET status = ?, committed_at = ? WHERE operation_id = ?",
                (PaperOperationStatus.ABORTED.value, now.isoformat(), operation_id),
            )
            self._append_journal(
                connection,
                event_id=f"operation-{operation_id}-aborted-{_time_key(now)}",
                event_type="OPERATION_ABORTED",
                occurred_at=now,
                payload={"operation_id": operation_id, "reason": reason},
            )
            updated = self._operation_row(connection, operation_id)
        return _operation_record(updated)

    def verify_integrity(self) -> PaperRuntimeSnapshot:
        self._verify_journal_chain()
        orchestrator, cycles, reconciliations = self._rebuild_committed(verify_snapshots=True)
        self._orchestrator = orchestrator
        self._cycle_results = cycles
        self._reconciliation_results = reconciliations
        return self.latest_snapshot

    def build_daily_report(self, report_date: date) -> DailyPaperSmokeReport:
        rows = self._connection.execute("SELECT * FROM operations ORDER BY sequence").fetchall()
        committed_sequences = [
            int(row["sequence"])
            for row in rows
            if row["status"] == PaperOperationStatus.COMMITTED.value
        ]
        high_water = max(committed_sequences, default=0)
        cycle_statuses: Counter[str] = Counter()
        cycle_count = 0
        reconciliation_count = 0
        mismatch_count = 0
        aborted = 0
        pending = 0
        executed_quantity = Decimal("0")
        fees = Decimal("0")
        first_event: int | None = None
        last_event: int | None = None
        for row in rows:
            status = PaperOperationStatus(row["status"])
            started_date = _datetime(row["started_at"]).date()
            if status is PaperOperationStatus.PENDING and started_date == report_date:
                pending += 1
            if status is PaperOperationStatus.ABORTED and started_date == report_date:
                aborted += 1
            if status is not PaperOperationStatus.COMMITTED or row["result_json"] is None:
                continue
            request = json.loads(row["request_json"])
            occurred_date = date.fromisoformat(str(request["occurred_at"])[:10])
            if occurred_date != report_date:
                continue
            result = json.loads(row["result_json"])
            kind = PaperOperationKind(row["kind"])
            if kind is PaperOperationKind.CYCLE:
                cycle_count += 1
                cycle_statuses[str(result["status"])] += 1
                executed_quantity += Decimal(str(result["executed_quantity"]))
                fill = result.get("venue_fill")
                if isinstance(fill, dict):
                    fees += Decimal(str(fill["fee_amount"]))
                event_start = int(result["event_sequence_start"])
                event_end = int(result["event_sequence_end"])
                first_event = event_start if first_event is None else min(first_event, event_start)
                last_event = event_end if last_event is None else max(last_event, event_end)
            else:
                reconciliation_count += 1
                if not bool(result["matched"]):
                    mismatch_count += 1
        findings: list[str] = []
        if pending:
            findings.append(f"{pending} operation(s) remain pending")
        if aborted:
            findings.append(f"{aborted} operation(s) were aborted")
        if mismatch_count:
            findings.append(f"{mismatch_count} reconciliation mismatch(es) were recorded")
        blocked = cycle_statuses.get("POST_TRADE_BLOCKED", 0)
        if blocked:
            findings.append(f"{blocked} cycle(s) failed the post-trade risk gate")
        latest = self.latest_snapshot
        head = self._journal_head_for_operation(high_water)
        healthy = not findings
        report_id = f"{self.authorization.session_id}:{report_date.isoformat()}:op-{high_water}"
        return DailyPaperSmokeReport(
            report_id=report_id,
            session_id=self.authorization.session_id,
            report_date=report_date,
            high_water_operation_sequence=high_water,
            cycle_count=cycle_count,
            reconciliation_count=reconciliation_count,
            reconciliation_mismatch_count=mismatch_count,
            aborted_operation_count=aborted,
            pending_operation_count=pending,
            status_counts=tuple(sorted(cycle_statuses.items())),
            executed_quantity=executed_quantity,
            fee_amount=fees,
            first_event_sequence=first_event,
            last_event_sequence=last_event,
            latest_session_sha256=latest.session_sha256,
            journal_head_sha256=head,
            healthy=healthy,
            findings=tuple(findings),
        )

    def record_daily_report(
        self,
        report_date: date,
        *,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> DailyPaperSmokeReport:
        report = self.build_daily_report(report_date)
        report_json = report.to_json()
        digest = report.content_sha256()
        with self._transaction() as connection:
            self._require_lease(connection, lease, now)
            existing = connection.execute(
                "SELECT report_json, report_sha256 FROM reports WHERE report_id = ?",
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                if existing["report_json"] != report_json or existing["report_sha256"] != digest:
                    raise PaperRecoveryIntegrityError(
                        "stored daily report differs from deterministic reconstruction"
                    )
                return report
            connection.execute(
                """
                INSERT INTO reports(report_id, report_date, operation_sequence,
                                    report_json, report_sha256, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.report_date.isoformat(),
                    report.high_water_operation_sequence,
                    report_json,
                    digest,
                    now.isoformat(),
                ),
            )
            self._append_journal(
                connection,
                event_id=f"report-{report.report_id}-{digest[:16]}",
                event_type="DAILY_REPORT_RECORDED",
                occurred_at=now,
                payload={
                    "healthy": report.healthy,
                    "report_id": report.report_id,
                    "report_sha256": digest,
                },
            )
        return report

    def audit_json(self) -> str:
        operations = [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM operations ORDER BY sequence"
            ).fetchall()
        ]
        snapshots = [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM snapshots ORDER BY operation_sequence"
            ).fetchall()
        ]
        journal = [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM journal ORDER BY sequence"
            ).fetchall()
        ]
        reports = [
            dict(row)
            for row in self._connection.execute(
                "SELECT * FROM reports ORDER BY report_id"
            ).fetchall()
        ]
        payload = {
            "journal": journal,
            "metadata": dict(self._connection.execute("SELECT key, value FROM metadata")),
            "operations": operations,
            "reports": reports,
            "snapshots": snapshots,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _stage_operation(
        self,
        *,
        operation_id: str,
        kind: PaperOperationKind,
        request_json: str,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> PaperOperationRecord:
        _text(operation_id, "operation_id")
        _aware(now, "now")
        request_sha = _sha256(request_json)
        with self._transaction() as connection:
            self._require_lease(connection, lease, now)
            existing = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if existing["kind"] != kind.value or existing["request_sha256"] != request_sha:
                    raise PaperIdempotencyConflictError(
                        f"operation ID {operation_id!r} was reused with different evidence"
                    )
                return _operation_record(existing)
            pending = connection.execute(
                "SELECT operation_id FROM operations WHERE status = ? LIMIT 1",
                (PaperOperationStatus.PENDING.value,),
            ).fetchone()
            if pending is not None:
                pending_id = pending["operation_id"]
                raise PaperRuntimeError(
                    f"pending operation must be recovered or aborted first: {pending_id}"
                )
            cursor = connection.execute(
                """
                INSERT INTO operations(operation_id, kind, status, request_json,
                                       request_sha256, started_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    kind.value,
                    PaperOperationStatus.PENDING.value,
                    request_json,
                    request_sha,
                    now.isoformat(),
                ),
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None:
                raise PaperRecoveryIntegrityError(
                    "SQLite did not return an operation sequence"
                )
            sequence = lastrowid
            self._append_journal(
                connection,
                event_id=f"operation-{operation_id}-staged-{sequence}",
                event_type="OPERATION_STAGED",
                occurred_at=now,
                payload={
                    "kind": kind.value,
                    "operation_id": operation_id,
                    "request_sha256": request_sha,
                    "sequence": sequence,
                },
            )
            row = self._operation_row(connection, operation_id)
        return _operation_record(row)

    def _commit_pending(
        self,
        operation_id: str,
        *,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> PaperCycleResult | ReconciliationResult:
        _aware(now, "now")
        row = self._connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        status = PaperOperationStatus(row["status"])
        kind = PaperOperationKind(row["kind"])
        if status is PaperOperationStatus.COMMITTED:
            return (
                self._cycle_results[operation_id]
                if kind is PaperOperationKind.CYCLE
                else self._reconciliation_results[operation_id]
            )
        if status is PaperOperationStatus.ABORTED:
            raise PaperRuntimeError(f"paper operation is aborted: {operation_id}")

        candidate, cycle_results, reconciliation_results = self._rebuild_committed(
            verify_snapshots=True
        )
        if kind is PaperOperationKind.CYCLE:
            cycle_result = candidate.run_cycle(
                _cycle_request_from_json(row["request_json"])
            )
            result: PaperCycleResult | ReconciliationResult = cycle_result
            cycle_results[operation_id] = cycle_result
        else:
            decision_id, occurred_at, broker = _reconciliation_request_from_json(
                row["request_json"]
            )
            reconciliation_result = candidate.reconcile(
                decision_id=decision_id,
                occurred_at=occurred_at,
                broker_snapshot=broker,
            )
            result = reconciliation_result
            reconciliation_results[operation_id] = reconciliation_result
        result_json = result.to_json()
        result_sha = _sha256(result_json)
        session_json = candidate.session_json()
        session_sha = _sha256(session_json)
        operation_sequence = int(row["sequence"])
        snapshot = _runtime_snapshot(candidate, operation_sequence, now, session_sha)

        with self._transaction() as connection:
            self._require_lease(connection, lease, now)
            current = self._operation_row(connection, operation_id)
            if current["status"] != PaperOperationStatus.PENDING.value:
                raise PaperRuntimeError("operation status changed during execution")
            connection.execute(
                """
                UPDATE operations
                SET status = ?, result_json = ?, result_sha256 = ?, committed_at = ?
                WHERE operation_id = ?
                """,
                (
                    PaperOperationStatus.COMMITTED.value,
                    result_json,
                    result_sha,
                    now.isoformat(),
                    operation_id,
                ),
            )
            self._insert_snapshot(connection, snapshot, session_json)
            self._append_journal(
                connection,
                event_id=f"operation-{operation_id}-committed-{operation_sequence}",
                event_type="OPERATION_COMMITTED",
                occurred_at=now,
                payload={
                    "kind": kind.value,
                    "operation_id": operation_id,
                    "operation_sequence": operation_sequence,
                    "result_sha256": result_sha,
                    "session_sha256": session_sha,
                },
            )
            self._append_journal(
                connection,
                event_id=f"snapshot-{snapshot.snapshot_id}",
                event_type="SNAPSHOT_WRITTEN",
                occurred_at=now,
                payload={
                    "event_sequence": snapshot.event_sequence,
                    "operation_sequence": operation_sequence,
                    "session_sha256": session_sha,
                    "snapshot_id": snapshot.snapshot_id,
                },
            )
        self._orchestrator = candidate
        self._cycle_results = cycle_results
        self._reconciliation_results = reconciliation_results
        return result

    def _rebuild_committed(
        self,
        *,
        verify_snapshots: bool,
    ) -> tuple[
        PaperTradingOrchestrator,
        dict[str, PaperCycleResult],
        dict[str, ReconciliationResult],
    ]:
        orchestrator = self._new_orchestrator()
        cycle_results: dict[str, PaperCycleResult] = {}
        reconciliation_results: dict[str, ReconciliationResult] = {}
        initial = self._connection.execute(
            "SELECT * FROM snapshots WHERE operation_sequence = 0"
        ).fetchone()
        if initial is None:
            raise PaperRecoveryIntegrityError("initial paper runtime snapshot is missing")
        if verify_snapshots:
            self._verify_snapshot_row(initial, orchestrator.session_json(), orchestrator)
        rows = self._connection.execute(
            "SELECT * FROM operations WHERE status = ? ORDER BY sequence",
            (PaperOperationStatus.COMMITTED.value,),
        ).fetchall()
        for row in rows:
            if _sha256(row["request_json"]) != row["request_sha256"]:
                raise PaperRecoveryIntegrityError(
                    f"operation {row['operation_id']!r} request digest is invalid"
                )
            kind = PaperOperationKind(row["kind"])
            operation_id = str(row["operation_id"])
            if kind is PaperOperationKind.CYCLE:
                cycle_result = orchestrator.run_cycle(
                    _cycle_request_from_json(row["request_json"])
                )
                result: PaperCycleResult | ReconciliationResult = cycle_result
                cycle_results[operation_id] = cycle_result
            else:
                decision_id, occurred_at, broker = _reconciliation_request_from_json(
                    row["request_json"]
                )
                reconciliation_result = orchestrator.reconcile(
                    decision_id=decision_id,
                    occurred_at=occurred_at,
                    broker_snapshot=broker,
                )
                result = reconciliation_result
                reconciliation_results[operation_id] = reconciliation_result
            result_json = result.to_json()
            if result_json != row["result_json"] or _sha256(result_json) != row["result_sha256"]:
                raise PaperRecoveryIntegrityError(
                    f"operation {operation_id!r} did not reproduce its committed result"
                )
            if verify_snapshots:
                snapshot = self._connection.execute(
                    "SELECT * FROM snapshots WHERE operation_sequence = ?",
                    (row["sequence"],),
                ).fetchone()
                if snapshot is None:
                    raise PaperRecoveryIntegrityError(
                        f"operation {operation_id!r} is missing a durable snapshot"
                    )
                self._verify_snapshot_row(snapshot, orchestrator.session_json(), orchestrator)
        return orchestrator, cycle_results, reconciliation_results

    def _verify_snapshot_row(
        self,
        row: sqlite3.Row,
        session_json: str,
        orchestrator: PaperTradingOrchestrator,
    ) -> None:
        checkpoint = orchestrator.latest_checkpoint
        checks = {
            "session_json": session_json,
            "session_sha256": _sha256(session_json),
            "checkpoint_id": checkpoint.checkpoint_id,
            "event_sequence": checkpoint.event_sequence,
            "event_log_sha256": checkpoint.event_log_sha256,
            "state_sha256": checkpoint.state_sha256,
        }
        for name, expected in checks.items():
            if row[name] != expected:
                raise PaperRecoveryIntegrityError(
                    f"snapshot {row['snapshot_id']!r} differs at {name}"
                )

    def _new_orchestrator(self) -> PaperTradingOrchestrator:
        return PaperTradingOrchestrator(
            plugin=self.plugin,
            decision_package=self.decision_package,
            authorization=self.authorization,
            venue_profile=self.venue_profile,
            risk_policy=self.risk_policy,
            venue_config=self.venue_config,
        )

    def _initialize_store(self) -> None:
        orchestrator = self._new_orchestrator()
        session_json = orchestrator.session_json()
        identity = self._identity_values()
        snapshot = _runtime_snapshot(
            orchestrator,
            operation_sequence=0,
            created_at=self.authorization.authorized_at,
            session_sha256=_sha256(session_json),
        )
        with self._transaction() as connection:
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                sorted(identity.items()),
            )
            self._insert_snapshot(connection, snapshot, session_json)
            self._append_journal(
                connection,
                event_id=f"runtime-{self.authorization.session_id}-initialized",
                event_type="RUNTIME_INITIALIZED",
                occurred_at=self.authorization.authorized_at,
                payload={
                    "identity_sha256": identity["identity_sha256"],
                    "session_id": self.authorization.session_id,
                    "session_sha256": snapshot.session_sha256,
                },
            )

    def _validate_store_identity(self) -> None:
        expected = self._identity_values()
        actual = dict(self._connection.execute("SELECT key, value FROM metadata"))
        if actual != expected:
            different = sorted(
                key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key)
            )
            raise PaperRecoveryIntegrityError(
                "durable runtime identity mismatch: " + ", ".join(different)
            )

    def _identity_values(self) -> dict[str, str]:
        values = {
            "authorization_sha256": _sha256(_canonical_json(self.authorization.to_dict())),
            "decision_package_sha256": self.decision_package.content_sha256(),
            "plugin_name": self.plugin.name,
            "risk_policy_sha256": _sha256(_canonical_json(self.risk_policy)),
            "schema_version": SCHEMA_VERSION,
            "session_id": self.authorization.session_id,
            "venue_config_sha256": _sha256(_canonical_json(self.venue_config)),
            "venue_profile_sha256": _sha256(_canonical_json(self.venue_profile)),
        }
        values["identity_sha256"] = _sha256(_canonical_json(values))
        return values

    def _configure_database(self) -> None:
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leases(
                session_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                result_json TEXT,
                result_sha256 TEXT,
                started_at TEXT NOT NULL,
                committed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots(
                snapshot_id TEXT PRIMARY KEY,
                operation_sequence INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                session_json TEXT NOT NULL,
                session_sha256 TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                event_sequence INTEGER NOT NULL,
                event_log_sha256 TEXT NOT NULL,
                state_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS journal(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_sha256 TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS reports(
                report_id TEXT PRIMARY KEY,
                report_date TEXT NOT NULL,
                operation_sequence INTEGER NOT NULL,
                report_json TEXT NOT NULL,
                report_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def _require_lease(
        self,
        connection: sqlite3.Connection,
        lease: PaperRuntimeLease,
        now: datetime,
    ) -> None:
        _aware(now, "now")
        if lease.session_id != self.authorization.session_id:
            raise PaperLeaseConflictError("lease session does not match the runtime")
        row = connection.execute(
            "SELECT * FROM leases WHERE session_id = ?",
            (self.authorization.session_id,),
        ).fetchone()
        if row is None:
            raise PaperLeaseConflictError("paper runtime has no active writer lease")
        if row["owner_id"] != lease.owner_id or int(row["fencing_token"]) != lease.fencing_token:
            raise PaperLeaseConflictError("paper writer lease was fenced by another owner")
        if _datetime(row["expires_at"]) <= now:
            raise PaperLeaseConflictError("paper writer lease has expired")

    def _append_journal(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        event_type: str,
        occurred_at: datetime,
        payload: Mapping[str, object],
    ) -> str:
        _text(event_id, "event_id")
        _text(event_type, "event_type")
        _aware(occurred_at, "occurred_at")
        previous = connection.execute(
            "SELECT entry_sha256 FROM journal ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_sha = GENESIS_SHA256 if previous is None else str(previous["entry_sha256"])
        payload_json = _canonical_json(payload)
        entry_sha = _journal_digest(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at.isoformat(),
            payload_json=payload_json,
            previous_sha256=previous_sha,
        )
        connection.execute(
            """
            INSERT INTO journal(event_id, event_type, occurred_at, payload_json,
                                previous_sha256, entry_sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_type,
                occurred_at.isoformat(),
                payload_json,
                previous_sha,
                entry_sha,
            ),
        )
        return entry_sha

    def _verify_journal_chain(self) -> None:
        previous = GENESIS_SHA256
        rows = self._connection.execute("SELECT * FROM journal ORDER BY sequence").fetchall()
        for row in rows:
            if row["previous_sha256"] != previous:
                raise PaperRecoveryIntegrityError(
                    f"journal chain broke before sequence {row['sequence']}"
                )
            expected = _journal_digest(
                event_id=row["event_id"],
                event_type=row["event_type"],
                occurred_at=row["occurred_at"],
                payload_json=row["payload_json"],
                previous_sha256=previous,
            )
            if row["entry_sha256"] != expected:
                raise PaperRecoveryIntegrityError(
                    f"journal entry {row['sequence']} digest is invalid"
                )
            previous = expected

    def _journal_head_for_operation(self, operation_sequence: int) -> str:
        if operation_sequence == 0:
            row = self._connection.execute(
                "SELECT entry_sha256 FROM journal WHERE event_type = ? ORDER BY sequence LIMIT 1",
                ("RUNTIME_INITIALIZED",),
            ).fetchone()
            return GENESIS_SHA256 if row is None else str(row["entry_sha256"])
        rows = self._connection.execute(
            "SELECT payload_json, entry_sha256 FROM journal WHERE event_type = ? ORDER BY sequence",
            ("SNAPSHOT_WRITTEN",),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if int(payload["operation_sequence"]) == operation_sequence:
                return str(row["entry_sha256"])
        raise PaperRecoveryIntegrityError(
            f"journal has no snapshot event for operation {operation_sequence}"
        )

    def _insert_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: PaperRuntimeSnapshot,
        session_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO snapshots(snapshot_id, operation_sequence, created_at,
                                  session_json, session_sha256, checkpoint_id,
                                  event_sequence, event_log_sha256, state_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.operation_sequence,
                snapshot.created_at.isoformat(),
                session_json,
                snapshot.session_sha256,
                snapshot.checkpoint_id,
                snapshot.event_sequence,
                snapshot.event_log_sha256,
                snapshot.state_sha256,
            ),
        )

    def _operation_row(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(operation_id)
        if not isinstance(row, sqlite3.Row):
            raise PaperRecoveryIntegrityError(
                "SQLite row factory returned an invalid row"
            )
        return row

    def _metadata_value(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])


def _runtime_snapshot(
    orchestrator: PaperTradingOrchestrator,
    operation_sequence: int,
    created_at: datetime,
    session_sha256: str,
) -> PaperRuntimeSnapshot:
    checkpoint = orchestrator.latest_checkpoint
    return PaperRuntimeSnapshot(
        snapshot_id=(
            f"paper-{orchestrator.authorization.session_id}-operation-{operation_sequence}"
        ),
        operation_sequence=operation_sequence,
        created_at=created_at,
        session_sha256=session_sha256,
        checkpoint_id=checkpoint.checkpoint_id,
        event_sequence=checkpoint.event_sequence,
        event_log_sha256=checkpoint.event_log_sha256,
        state_sha256=checkpoint.state_sha256,
    )


def _operation_record(row: sqlite3.Row) -> PaperOperationRecord:
    return PaperOperationRecord(
        sequence=int(row["sequence"]),
        operation_id=str(row["operation_id"]),
        kind=PaperOperationKind(row["kind"]),
        status=PaperOperationStatus(row["status"]),
        request_sha256=str(row["request_sha256"]),
        result_sha256=None if row["result_sha256"] is None else str(row["result_sha256"]),
        started_at=_datetime(row["started_at"]),
        committed_at=None if row["committed_at"] is None else _datetime(row["committed_at"]),
    )


def _snapshot_record(row: sqlite3.Row) -> PaperRuntimeSnapshot:
    return PaperRuntimeSnapshot(
        snapshot_id=str(row["snapshot_id"]),
        operation_sequence=int(row["operation_sequence"]),
        created_at=_datetime(row["created_at"]),
        session_sha256=str(row["session_sha256"]),
        checkpoint_id=str(row["checkpoint_id"]),
        event_sequence=int(row["event_sequence"]),
        event_log_sha256=str(row["event_log_sha256"]),
        state_sha256=str(row["state_sha256"]),
    )


def _cycle_request_json(request: PaperCycleRequest) -> str:
    payload = {
        "completed_bars": [
            {
                "close": bar.close,
                "high": bar.high,
                "low": bar.low,
                "open": bar.open,
                "symbol": bar.symbol,
                "timestamp": bar.timestamp.isoformat(),
                "volume": bar.volume,
            }
            for bar in request.completed_bars
        ],
        "cycle_id": request.cycle_id,
        "daily_pnl": str(request.daily_pnl),
        "decision_quote": _quote_dict(request.decision_quote),
        "match_quote": _quote_dict(request.match_quote),
        "occurred_at": request.occurred_at.isoformat(),
        "reduce_only": request.reduce_only,
    }
    return _canonical_json(payload)


def _cycle_request_from_json(payload_json: str) -> PaperCycleRequest:
    payload = json.loads(payload_json)
    bars = tuple(
        MarketBar(
            symbol=str(item["symbol"]),
            timestamp=_datetime(item["timestamp"]),
            open=float(item["open"]),
            high=float(item["high"]),
            low=float(item["low"]),
            close=float(item["close"]),
            volume=float(item["volume"]),
        )
        for item in payload["completed_bars"]
    )
    return PaperCycleRequest(
        cycle_id=str(payload["cycle_id"]),
        occurred_at=_datetime(payload["occurred_at"]),
        completed_bars=bars,
        decision_quote=_quote_from_dict(payload["decision_quote"]),
        match_quote=_quote_from_dict(payload["match_quote"]),
        daily_pnl=Decimal(str(payload["daily_pnl"])),
        reduce_only=bool(payload["reduce_only"]),
    )


def _quote_dict(quote: VenueQuote) -> dict[str, str]:
    return {
        "ask_price": str(quote.ask_price),
        "ask_quantity": str(quote.ask_quantity),
        "bid_price": str(quote.bid_price),
        "bid_quantity": str(quote.bid_quantity),
        "observed_at": quote.observed_at.isoformat(),
        "quote_id": quote.quote_id,
        "symbol": quote.symbol,
        "trade_price": str(quote.trade_price),
        "trade_volume": str(quote.trade_volume),
    }


def _quote_from_dict(payload: Mapping[str, object]) -> VenueQuote:
    return VenueQuote(
        quote_id=str(payload["quote_id"]),
        observed_at=_datetime(payload["observed_at"]),
        symbol=str(payload["symbol"]),
        bid_price=Decimal(str(payload["bid_price"])),
        ask_price=Decimal(str(payload["ask_price"])),
        bid_quantity=Decimal(str(payload["bid_quantity"])),
        ask_quantity=Decimal(str(payload["ask_quantity"])),
        trade_price=Decimal(str(payload["trade_price"])),
        trade_volume=Decimal(str(payload["trade_volume"])),
    )


def _reconciliation_request_json(
    *,
    decision_id: str,
    occurred_at: datetime,
    broker_snapshot: BrokerSnapshot,
) -> str:
    payload = {
        "broker_snapshot": {
            "account_id": broker_snapshot.account_id,
            "cash_balance": str(broker_snapshot.cash_balance),
            "currency": broker_snapshot.currency,
            "latest_event_sequence": broker_snapshot.latest_event_sequence,
            "observed_at": broker_snapshot.observed_at.isoformat(),
            "position_quantity": str(broker_snapshot.position_quantity),
            "snapshot_id": broker_snapshot.snapshot_id,
            "symbol": broker_snapshot.symbol,
        },
        "decision_id": decision_id,
        "occurred_at": occurred_at.isoformat(),
    }
    return _canonical_json(payload)


def _reconciliation_request_from_json(
    payload_json: str,
) -> tuple[str, datetime, BrokerSnapshot]:
    payload = json.loads(payload_json)
    broker = payload["broker_snapshot"]
    return (
        str(payload["decision_id"]),
        _datetime(payload["occurred_at"]),
        BrokerSnapshot(
            snapshot_id=str(broker["snapshot_id"]),
            observed_at=_datetime(broker["observed_at"]),
            account_id=str(broker["account_id"]),
            symbol=str(broker["symbol"]),
            currency=str(broker["currency"]),
            position_quantity=Decimal(str(broker["position_quantity"])),
            cash_balance=Decimal(str(broker["cash_balance"])),
            latest_event_sequence=(
                None
                if broker["latest_event_sequence"] is None
                else int(broker["latest_event_sequence"])
            ),
        ),
    )


def _journal_digest(
    *,
    event_id: str,
    event_type: str,
    occurred_at: str,
    payload_json: str,
    previous_sha256: str,
) -> str:
    return _sha256(
        _canonical_json(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "payload": json.loads(payload_json),
                "previous_sha256": previous_sha256,
            }
        )
    )


def _canonical_json(value: object) -> str:
    return json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"))


def _canonicalize(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("canonical evidence must not contain non-finite floats")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical evidence must not contain non-finite decimals")
        return str(value)
    if isinstance(value, datetime):
        _aware(value, "datetime")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return {
            "days": value.days,
            "microseconds": value.microseconds,
            "seconds": value.seconds,
        }
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if is_dataclass(value):
        return {
            field.name: _canonicalize(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    raise TypeError(f"unsupported canonical evidence type: {type(value).__name__}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _datetime(value: object) -> datetime:
    result = datetime.fromisoformat(str(value))
    _aware(result, "datetime")
    return result


def _time_key(value: datetime) -> str:
    return value.isoformat().replace(":", "").replace("+", "p")


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "DurablePaperRuntime",
    "PaperIdempotencyConflictError",
    "PaperLeaseConflictError",
    "PaperRecoveryIntegrityError",
    "PaperRuntimeError",
]
