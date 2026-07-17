# Financial ledger contract

This document defines the public boundary for execution assumptions, signed financial events, tax lots, and tax estimates. It is a domain contract, not a jurisdiction-specific tax engine and not investment or tax advice.

## Ownership boundary

`quant-platform` owns reusable types and invariants:

- `ExecutionRealityProfile`
- `ExecutionIntent`, `ExecutionOrder`, and `ExecutionFill`
- `FinancialLedgerEntry`, `FinancialLedger`, and deterministic summaries
- `TaxProfile`, `TaxLot`, `TaxableEvent`, and `TaxEstimate`

Concrete venue schedules, account credentials, private strategy parameters, country-specific rates, and operator decisions belong outside the public core. A tax rule whose classification is uncertain must use `TaxConfidence.REVIEW_REQUIRED`; the core must not guess.

## Calculation boundary

```text
Gross realized PnL
  + execution adjustments
  + financing adjustments
  = Economic Net PnL

Economic Net PnL
  - separate annual TaxEstimate
  = Estimated After-tax PnL
```

Execution adjustments include commission, rebate, spread, slippage, market impact, transaction taxes, and FX costs. Financing adjustments include funding, borrow interest, and margin interest. Deposits and withdrawals are external cash movements and do not count as strategy PnL. Annual income-tax estimates are not represented as execution costs or basis points.

## Signed amount convention

Every `FinancialLedgerEntry.amount` is from the account's perspective:

- positive: increases account value
- negative: decreases account value

Cost entries such as commission and slippage must be non-positive. Rebates must be non-negative. Funding may be positive or negative. Tax withholding and actual tax payments are tracked separately from Economic Net PnL for cash reconciliation.

## Currency rule

The ledger never performs implicit FX conversion. A summary requires an explicit currency and includes only entries already denominated in that currency. Conversion must produce an explicit `FX_COST` entry and converted financial events under a documented model.

## Ordering and identity invariants

- entry IDs are unique
- entries are chronological
- all timestamps are timezone-aware
- monetary values use `Decimal`
- order and fill quantities and prices are positive
- limit orders require a limit price; market orders reject one
- taxable amount equals gross amount minus deductible amount
- tax rules carry a version, source reference, effective period, and confidence

## Hand-calculated example

For one account in USD:

```text
Realized PnL          +100.0
Commission              -2.0
Slippage                -1.0
Funding                 +3.0
Borrow interest          -0.5
Transaction tax          -0.2
--------------------------------
Economic Net PnL        +99.3
```

A separate estimated annual tax of 20.0 produces an Estimated After-tax PnL of 79.3. It does not change the execution adjustment of -3.2.

## Backtest compatibility

`BacktestConfig.fee_bps` and `slippage_bps` remain a transitional minimal interface. A later adapter may emit ledger entries from those assumptions, but the current backtester is not silently changed by this contract. Venue profiles and tax rules will be integrated in separate work units with explicit regression tests.
