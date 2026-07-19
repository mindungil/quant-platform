# Durable Paper Runtime

`DurablePaperRuntime` turns the deterministic in-memory Paper cycle into a restartable single-node runtime. It does not change strategy logic or venue semantics. It adds durable command ordering, single-writer fencing, crash recovery, integrity verification, reconciliation persistence, and daily operational evidence around the existing `PaperTradingOrchestrator`.

## Runtime boundary

The reference runtime is deliberately narrow:

- one approved `PAPER` strategy package
- one Paper session, account, symbol, and settlement currency
- one point-in-time strategy plugin
- one versioned venue snapshot and venue simulation configuration
- one versioned single-strategy risk policy
- one SQLite database file on one node

It does not provide broker connectivity, market-data subscriptions, scheduling, alert delivery, distributed consensus, multi-strategy allocation, or automatic lifecycle promotion.

## Durable identity

A database is permanently bound to the following immutable inputs:

- Paper launch authorization
- Strategy Decision Package digest
- plugin name
- venue profile snapshot
- venue simulation configuration
- risk policy
- runtime schema version
- session ID

Canonical SHA-256 fingerprints are written at initialization. Reopening a database with a different package, risk policy, venue profile, or configuration fails closed before any command is executed.

This prevents a long-running Paper history from silently continuing under changed assumptions.

## SQLite durability settings

The reference implementation enables:

```text
journal_mode = WAL
synchronous = FULL
foreign_keys = ON
busy_timeout = 5000 ms
```

`FULL` synchronous mode asks SQLite to wait for the operating system to durably flush transaction-critical data. WAL permits readers while the single fenced writer commits commands. These settings improve single-node crash consistency but do not replace storage-level backups, disk health monitoring, or distributed replication.

## Command model

The runtime persists two command kinds:

- `CYCLE` — one `PaperCycleRequest`
- `RECONCILIATION` — one external `BrokerSnapshot` comparison

Every command moves through an explicit state:

```text
PENDING → COMMITTED
       ↘ ABORTED
```

### Stage before execution

A request is canonicalized and written as `PENDING` before it is evaluated. The row records:

- monotonic operation sequence
- operation ID
- command kind
- canonical request JSON
- request SHA-256
- start time

Only one unresolved `PENDING` operation is allowed. Later work is blocked until the pending command is recovered or explicitly aborted. This preserves command order and prevents a later cycle from overtaking an uncertain earlier cycle.

### Evaluate on a fresh replay

The runtime does not mutate the active orchestrator and then attempt to save it. Instead it:

1. creates a fresh `PaperTradingOrchestrator` from immutable launch inputs;
2. replays every committed command in sequence;
3. verifies each reconstructed result against its stored JSON and digest;
4. verifies the stored session snapshot and risk checkpoint after each command;
5. evaluates the pending command on this verified candidate orchestrator;
6. atomically stores the result, complete session snapshot, and journal evidence;
7. replaces the active in-memory orchestrator only after the transaction commits.

An exception before commit therefore leaves the previously committed runtime state unchanged. The command remains `PENDING` and can be retried after restart.

## Idempotency

The operation ID is the idempotency key.

- same ID + same command kind + same request digest: return the existing staged or committed operation;
- same ID + different command kind or request digest: raise `PaperIdempotencyConflictError`;
- committed duplicate: return the reconstructed typed result without adding another operation or execution event.

Callers must derive operation IDs from stable upstream event identities. Random retry IDs defeat idempotency and are not recommended.

## Single-writer lease and fencing

A mutating operation requires `PaperRuntimeLease`.

The lease contains:

- session ID
- owner ID
- monotonic fencing token
- acquisition time
- expiry time

Rules:

- a different owner cannot acquire an unexpired lease;
- the current owner may renew without changing the token;
- takeover after expiry increments the fencing token;
- every mutating transaction rereads the database lease;
- an old owner holding a stale Python object is rejected after takeover;
- release requires the current owner and token.

The fencing token protects the SQLite reference runtime from stale local workers. It is not a distributed lock protocol for external broker APIs or multiple database replicas.

## Snapshots

Every committed operation writes a complete deterministic session snapshot containing:

- canonical `session_json`
- session SHA-256
- operation sequence
- risk checkpoint ID
- execution event sequence
- execution event-log SHA-256
- execution-state SHA-256

The initial launch state is stored at operation sequence zero. Recovery verifies every intermediate snapshot, not just the latest row, so corruption or nondeterminism is localized to the first failing operation.

## Append-only journal chain

Runtime lifecycle actions are written to a journal:

- runtime initialization
- lease acquire, renew, takeover, and release
- operation staged
- operation committed or aborted
- snapshot written
- recovery completed
- daily report recorded

Each entry stores the previous journal SHA-256 and its own SHA-256 over canonical entry content. `verify_integrity()` walks the complete chain and fails on modified payloads, missing links, or invalid entry digests.

The chain is tamper-evident inside the database. It is not independently immutable if an attacker can replace the entire database and all backups. Production operations should export journal heads to an external write-once or separately controlled system.

## Crash recovery

### Crash before staging

No operation exists. The caller may submit the command normally.

### Crash after `PENDING`, before commit

The command remains `PENDING`. On restart:

1. open the runtime with exactly the same immutable identity;
2. acquire a new lease after the old lease expires;
3. inspect `pending_operations`;
4. call `recover_pending()`.

The runtime rebuilds committed state and executes the exact persisted request. A successful recovery commits the result and snapshot with the existing operation sequence.

### Crash during SQLite commit

SQLite transaction atomicity determines visibility. On restart the row is either still `PENDING` or fully `COMMITTED` with its result and snapshot. A partially visible committed command is rejected by integrity verification.

### Unrecoverable upstream evidence

An operator may call `abort_pending()` with an explicit reason. Abortion is journaled and the command remains in history. The operation ID cannot later be reused as a different command.

Aborted operations and unresolved pending operations make the daily smoke report unhealthy.

## Reconciliation journal

Reconciliation is a first-class durable command, not an untracked read operation. The persisted request includes:

- decision ID
- reconciliation time
- broker snapshot ID and observation time
- account, symbol, and currency
- external position and cash
- optional external event sequence

The stored result contains the internal and external values plus the Risk Engine reconciliation decision. Recovery reruns the same comparison at the same point in command order and verifies byte-identical result JSON.

A mismatch is preserved as evidence; the runtime does not silently modify internal execution state to match the external snapshot.

## Daily smoke report

`build_daily_report(date)` creates deterministic JSON and Markdown from committed evidence. It includes:

- high-water operation sequence
- cycle and reconciliation counts
- cycle status distribution
- executed quantity and fees
- reconciliation mismatch count
- pending and aborted operation counts
- first and last execution-event sequence
- latest session digest
- journal head associated with the high-water operation
- health finding list

The report is unhealthy when it observes:

- unresolved pending operations;
- aborted operations;
- reconciliation mismatches;
- `POST_TRADE_BLOCKED` cycles.

`record_daily_report()` stores the report and digest idempotently and appends a journal entry. Re-recording the same report ID with different reconstructed bytes fails closed.

A healthy daily report means the recorded runtime evidence is internally consistent for that date. It is not a profitability, strategy-validation, tax, or production-readiness approval.

## Minimal recovery sequence

```python
from datetime import UTC, datetime, timedelta
from quant_platform.paper import DurablePaperRuntime

runtime = DurablePaperRuntime(
    "state/paper-session.db",
    plugin=plugin,
    decision_package=decision_package,
    authorization=authorization,
    venue_profile=venue_profile,
    risk_policy=risk_policy,
)

now = datetime.now(UTC)
lease = runtime.acquire_lease(
    owner_id="paper-worker-1",
    now=now,
    ttl=timedelta(minutes=2),
)

runtime.verify_integrity()
runtime.recover_pending(lease=lease, now=now)
```

Before scheduling new cycles, operators should also inspect:

- `pending_operations`
- `latest_snapshot`
- current lease owner and expiry in the database
- the latest recorded daily smoke report
- external broker or shadow snapshot reconciliation

## Backup and restore

The public reference core does not automate backups. A safe operations layer should:

- use SQLite's online backup API or a transactionally consistent snapshot;
- preserve the database, WAL, and relevant storage metadata correctly;
- periodically test restoration into a separate path;
- reopen the restored database with pinned immutable runtime inputs;
- run `verify_integrity()` before accepting any new command;
- compare the restored journal head and latest session digest with an external record.

Copying only the main database file while an active WAL exists can produce an incomplete backup.

## Promotion boundary

Implementing this durable runtime does not authorize Paper execution. The Strategy Decision Package must already have reached `PAPER` through an approved `PAPER_READINESS` decision. A successful Paper runtime period also does not automatically promote the strategy to `LIVE_CANDIDATE`.

The later Paper reconciliation gate must evaluate actual long-running evidence, operational incidents, costs, drift, and external reconciliation before any lifecycle transition.
