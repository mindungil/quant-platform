# Paper Trading Orchestrator

The reference paper orchestrator connects an already-approved strategy artifact to the public execution, venue, and risk engines. It is a deterministic simulator-backed workflow, not a broker client and not a promotion mechanism.

## Launch gate

A session can launch only from a `StrategyDecisionPackage` whose current lifecycle state is `PAPER` and whose history contains an approved decision targeting `PAPER`.

`PaperLaunchAuthorization` freezes:

- the Strategy Decision Package ID and content SHA-256;
- strategy, account, symbol, and settlement currency;
- execution-profile snapshot and risk-policy identifiers;
- authorizer, authorization time, and initial paper collateral;
- the paper orchestration model version.

The orchestrator rejects package-digest, plugin-name, symbol, currency, venue-profile, and risk-policy mismatches. A package in `IMPLEMENTED`, `DEVELOPMENT_VALIDATED`, or `HOLDOUT_VALIDATED` cannot be used to bypass the Paper-readiness gate.

## Deterministic cycle

Each cycle accepts completed bars, one decision quote, one match quote, and the current daily PnL evidence.

```text
completed bars
  → point-in-time signal
  → equity-scaled target quantity
  → pre-trade risk decision and optional size reduction
  → venue constraint check and order submission
  → deterministic single-quote matching
  → cancel any unfilled remainder
  → post-trade risk and invariant checks
  → immutable execution checkpoint
  → caller-supplied external snapshot reconciliation
```

The strategy signal must reference the newest completed bar and the authorized symbol. Decision data must not come from the future or regress behind existing execution events.

Approved quantities are floored to the venue's versioned market quantity increment. They are never silently rounded upward. The venue simulator remains the source of order-constraint, fill, fee, latency, liquidity-role, and volume-participation evidence.

## Cycle outcomes

The result records one explicit status:

- `NO_ACTION`: target already matches the current position;
- `RISK_REJECTED`: the pre-trade gate failed;
- `BELOW_VENUE_INCREMENT`: the allowed size cannot form a valid venue quantity;
- `VENUE_REJECTED`: versioned venue constraints rejected the order;
- `NO_FILL`: the single match quote produced no fill;
- `PARTIALLY_FILLED`: some quantity filled and the remainder was cancelled;
- `FILLED`: the requested paper order filled and passed post-trade checks;
- `POST_TRADE_BLOCKED`: execution state failed a post-trade invariant and the automatic kill switch engaged.

Every result includes the signal, target and order quantities, risk decisions, venue order/fill evidence, optional kill-switch event, event-sequence range, and checkpoint digests.

## Checkpoint and reconciliation

`RiskCheckpoint` freezes the execution-event count, event-log SHA-256, and state SHA-256 at the end of each cycle. `session_json()` combines the authorization, all cycles, execution events and state, latest checkpoint, and risk audit into a deterministic session artifact.

Reconciliation is intentionally not self-certifying. The caller must provide an external simulator or broker-style `BrokerSnapshot`. The risk engine compares position, settlement cash, and event sequence against internal state using the configured tolerances.

## Failure and recovery

When a post-trade gate fails, the orchestrator engages the existing automatic kill switch. Recovery must follow the single-strategy risk runbook:

1. stop new exposure-increasing cycles;
2. preserve the session JSON, execution events, and risk audit;
3. replay the execution ledger and verify checkpoint digests;
4. obtain a fresh external snapshot and reconcile it;
5. reduce exposure only through an explicitly valid reduce-only path when necessary;
6. create a fresh checkpoint after invariants are restored;
7. clear the kill switch only through an explicit recovery event.

A process restart is not recovery evidence.

## Reference-model boundary

Version 1 intentionally supports:

- one strategy, account, symbol, and settlement currency;
- a point-in-time `AlphaPlugin`;
- derivative PnL-only settlement;
- one decision quote and one match quote per cycle;
- deterministic cancellation of all residual quantity;
- in-memory event and audit state.

It intentionally excludes:

- real broker transport and credentials;
- scheduling, market-data subscriptions, and retry workers;
- persistent storage and distributed coordination;
- alert delivery and operator escalation;
- cross-margin, portfolio offsets, and multi-strategy allocation;
- automatic lifecycle promotion or Live authorization;
- persistent multi-quote limit-order management.

Private operations code may assemble these contracts after independent validation, but production configuration and strategy implementations do not belong in the public repository.
