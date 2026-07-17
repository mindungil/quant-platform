# Versioned execution profile snapshots

Execution profiles are frozen research inputs, not timeless venue constants.

A reproducible execution snapshot combines:

- the generic `ExecutionRealityProfile` used by simulation adapters,
- symbol-level `InstrumentExecutionRules`,
- the exact source references and SHA-256 digests,
- observation and effective timestamps,
- an explicit confidence state.

## Why a snapshot is required

Exchange rules and account commissions can change independently. A backtest must
not silently read today's filters or fee tier when reproducing an older result.

The expected flow is:

```text
official exchange/account response
  -> immutable raw payload
  -> source SHA-256
  -> ExecutionProfileSnapshot
  -> order validation and ledger entries
  -> experiment manifest
```

Concrete exchange parsers and account credentials remain outside the public
package. The open-core package owns only deterministic contracts and pure
validation helpers.

## Order constraints

`check_order_constraints` mirrors venue filters without silently changing an
order. It checks:

- price minimum, maximum, and tick,
- quantity minimum, maximum, and step,
- market-order quantity overrides,
- minimum and maximum notional,
- contract multiplier.

A disabled venue rule must be represented by `None`, not by an invented
precision. `floor_to_increment` is a separate opt-in helper so callers must
record any rounding decision explicitly.

Market orders require an explicit reference price whenever a notional filter
applies. The public core never chooses a last price, mark price, or average
price on behalf of an adapter.

## Fee and funding entries

`settlement_value_fee_entry` is deliberately narrow. It is valid only when a
venue charges maker/taker fees from quote notional in the profile settlement
currency, as with a simple USD-margined futures fill.

Do not use it for:

- Spot BUY fees deducted from the received base asset,
- fees paid in a third asset such as BNB,
- side-specific, special, or transaction-tax commission components,
- fee conversions that require an FX price.

Those cases require a venue-specific adapter that emits one or more
`FinancialLedgerEntry` records in their actual currencies.

`funding_ledger_entry` preserves the signed account perspective:

- positive amount: funding received,
- negative amount: funding paid.

## Ownership boundary

`quant-platform` owns:

- snapshot and evidence contracts,
- symbol-rule validation,
- Decimal-exact notional calculation,
- generic signed ledger-entry helpers.

Private integration packages own:

- authenticated commission endpoint access,
- venue response parsing,
- BNB or other fee-asset conversion,
- concrete fee tiers, promotions, and rebates,
- broker or exchange profile snapshots.
