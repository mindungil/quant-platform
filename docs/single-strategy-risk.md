# Single-Strategy Risk Engine and Recovery Runbook

`SingleStrategyRiskEngine` is a deterministic, fail-closed control layer for one strategy, account, symbol, and settlement currency. It records every risk decision and kill-switch transition as an ordered audit artifact.

## Policy boundary

`SingleStrategyRiskPolicy` freezes:

- maximum order notional,
- maximum projected position notional,
- maximum projected leverage,
- maximum daily loss,
- maximum market-data age,
- broker-reconciliation tolerances, and
- whether an oversized but otherwise valid order may be reduced.

The policy does not infer limits from historical performance. Callers must version and review the values before Paper execution.

## Pre-trade gate

A `PreTradeRiskRequest` includes the current position, requested side and quantity, reference price, equity, daily PnL, and the timestamp of the market data used for the decision.

The gate checks:

1. strategy symbol and data-time consistency,
2. stale market data,
3. daily loss and positive-equity requirements,
4. requested order notional,
5. projected position notional, and
6. projected leverage.

When only sizing limits fail and the policy permits resizing, the decision remains allowed with a `size_multiplier` and explicit allowed quantity evidence. Structural failures such as stale data, future data, daily-loss exhaustion, and non-positive equity fail closed.

## Kill switch and reduce-only escape

A kill switch may be engaged manually or automatically after post-trade, stream-health, margin, or reconciliation failure.

While engaged:

- new exposure is blocked,
- exposure-increasing orders are blocked,
- reversing orders are blocked, and
- an explicitly marked `reduce_only` order is allowed only when it strictly reduces the absolute position without crossing through zero.

This exception is deliberate: a safety control must not prevent an operator from closing existing risk.

Clearing the switch requires a separate `RECOVERY` audit event and a non-empty reason. Clearing the switch does not erase earlier decisions or failures.

## Post-trade and stream health

Post-trade checks verify:

- contiguous event sequence,
- unique event IDs,
- non-decreasing event time,
- byte-identical replayed event and state JSON,
- finite cash and position accounting values, and
- a positive margin excess when a margin snapshot is supplied.

A `RiskCheckpoint` pins the expected event sequence, event-log SHA-256, and state SHA-256. Stream-health checks compare the current engine against that checkpoint and verify market-data freshness. A sequence or digest difference is treated as a missed, unexpected, or divergent event until reconciled.

## External reconciliation

`BrokerSnapshot` represents a read-only simulator or broker observation. Reconciliation compares:

- position quantity,
- settlement-currency cash, and
- latest event sequence when the external system supplies one.

Differences outside the configured tolerances generate blocking violations and automatically engage the kill switch. No implicit FX conversion or portfolio-level offset is performed.

## Audit evidence

Each decision retains:

- a contiguous audit sequence,
- policy ID,
- decision type and time,
- allow/block result and size multiplier,
- every violation with actual and limit values, and
- decision metrics and state digests.

`audit_json()` merges risk decisions and kill-switch events into one deterministic ordered JSON artifact. Two identical workflows must produce identical bytes.

## Minimum recovery scenario

Use this procedure before clearing an automatic or manual kill switch:

1. **Freeze new risk.** Confirm the switch is engaged and reject all non-reducing orders.
2. **Capture evidence.** Persist the current risk audit JSON, execution event JSON, execution-state JSON, policy ID, and latest external snapshot ID.
3. **Verify market data.** Confirm the newest observation is not from the future and is within `max_data_age`.
4. **Replay the ledger.** Rebuild `EventSourcedExecutionEngine` from the complete event tuple and require byte-identical event and state JSON.
5. **Reconcile externally.** Compare internal position, cash, and sequence against the simulator or broker snapshot within the frozen tolerances.
6. **Reduce exposure when needed.** Use explicit non-reversing `reduce_only` orders while the switch remains engaged.
7. **Re-check margin.** Require a non-liquidatable account and positive margin excess after any reduction.
8. **Create a fresh checkpoint.** Pin the reconciled event sequence and event/state digests.
9. **Clear deliberately.** Append a `RECOVERY` kill-switch event with the incident or review reference in the reason.
10. **Run a no-op gate.** Submit a zero-risk health and reconciliation cycle before allowing the next exposure-increasing order.

Do not clear the switch merely because a process restarted. A restart without replay, reconciliation, and a fresh checkpoint is not recovery evidence.

## Deliberate exclusions

This reference layer does not provide:

- portfolio or multi-strategy limits,
- cross-margin offsets,
- live alert delivery,
- broker transport or order submission,
- automated incident ownership,
- persistent storage, or
- deployment-specific monitoring and escalation.

Those responsibilities belong to later portfolio and operations layers and must consume, rather than rewrite, the immutable risk audit evidence.
