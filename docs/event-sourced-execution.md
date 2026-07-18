# Event-Sourced Execution Core

The public execution core records every state change as an immutable, ordered event and derives current orders, fills, positions, and cash balances by replaying that event log.

## Deterministic clock

Every event has:

- a unique `event_id`,
- a contiguous sequence number beginning at 1,
- a timezone-aware timestamp,
- a timestamp that never precedes the previous event.

Events with the same timestamp remain deterministic because sequence order is explicit. Replaying the same event tuple must reproduce byte-identical event and state JSON.

## Order lifecycle

```text
SUBMITTED
  ├─> ACCEPTED
  │     ├─> PARTIALLY_FILLED ─> FILLED
  │     └─> CANCELLED
  ├─> CANCELLED
  └─> REJECTED
```

A fill is accepted only after order acceptance. Cumulative fill quantity cannot exceed submitted quantity. Filled, cancelled, and rejected orders are terminal.

## Position accounting

Fills use signed quantities internally:

- BUY is positive,
- SELL is negative.

The engine maintains:

- signed position quantity,
- average entry price,
- cumulative realized PnL,
- latest mark price,
- unrealized PnL.

Partial closes retain the existing average price. Position reversals realize the closed portion and open the remaining quantity at the reversal fill price.

## Cash settlement modes

### `SPOT_NOTIONAL`

Cash changes by signed trade notional and fee:

```text
cash_delta = -(signed_quantity × fill_price) - fee
```

This is the reference model for directly cash-settled asset purchases and sales.

### `DERIVATIVE_PNL_ONLY`

Opening notional does not move cash. Cash changes only by realized PnL and fee:

```text
cash_delta = realized_pnl_delta - fee
```

This is a narrow reference model. Venue-specific margin reservation, variation margin, collateral haircuts, liquidation, and contract multipliers belong in later venue adapters.

## Independent cash events

Deposits, withdrawals, commissions, funding, interest, taxes, and other signed adjustments are recorded as append-only cash events. Balances are keyed by account and currency; no implicit FX conversion occurs.

## Reference strategy adapter

`ReferenceExecutionAdapter` evaluates one `BatchAlphaPlugin` through both the vectorized and event-driven paths.

The strategy is invoked once. Its complete target-position sequence is frozen as one artifact and then supplied unchanged to:

1. `BacktestRunner`, through an internal immutable batch plugin; and
2. `EventSourcedExecutionEngine`, through deterministic target-to-order conversion.

This prevents a stateful or non-deterministic strategy implementation from returning different targets to the two paths during one comparison.

For each interval, the event path:

- reads the target active from the current bar open,
- calculates desired quantity from current account equity and current open price,
- creates a market order for the position delta,
- records submit, accept, and fill events,
- applies explicit fee and directional slippage assumptions,
- marks the open position at the next bar open.

With zero fees and zero slippage, the constant-position reference scenario matches the vectorized ending equity, apart from normal binary-float representation tolerance. With costs enabled, the event path retains the concrete order, fill price, fee, cash, position, and replay evidence behind the result.

The comparison is intentionally a reference invariant, not a claim that the two engines must remain numerically identical under every cost model. The vectorized runner charges an abstract turnover fraction, while the event path rebalances quantities at explicit fill prices against current equity.

## Deliberate limitations

This core and reference adapter do not yet implement:

- venue order matching,
- partial fill generation by market liquidity,
- latency or stale-quote behavior,
- margin reservation and liquidation,
- tick, lot, and minimum-notional enforcement,
- reconciliation against a broker or exchange.

Those behaviors should consume this state machine rather than mutate its history.