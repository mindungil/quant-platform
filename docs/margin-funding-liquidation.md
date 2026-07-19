# Margin, Funding, and Liquidation Reference Model

This document defines the deliberately narrow financing and risk model implemented by `IsolatedMarginSimulator`.

## Versioned profile

`VersionedVenueProfile` binds:

- one immutable `ExecutionProfileSnapshot`, and
- one `IsolatedMarginProfile` that references that exact snapshot ID.

The wrapper exposes the execution snapshot fields used by `DeterministicVenueSimulator`, so matching, backtest, and paper-style execution can consume the same profile object and profile key.

The execution and margin settlement currencies must match. The current reference implementation accepts only a unit contract multiplier because the shared execution engine accounts for linear quantity-price PnL.

## Funding schedule

Funding is applied only at timestamps aligned to a declared `PeriodicFundingSchedule`.

For a signed position quantity `q`, mark price `p`, and funding rate `r`:

```text
funding cash flow = -(q × p × r)
```

A positive rate therefore charges a long position and credits a short position. The simulator rejects a second funding event for the same account, symbol, and scheduled timestamp.

The funding rate itself is explicit input evidence. The simulator does not infer or forecast a rate.

## Borrow and margin interest

Borrow and margin interest use an explicit principal, annual rate, and elapsed duration:

```text
interest cash flow = -(principal × annual rate × elapsed seconds / 365 days)
```

Both charges are append-only negative `INTEREST` cash events. A profile may omit either annual rate; attempting to accrue an omitted charge fails closed.

## Isolated-margin account state

The model is single-symbol and single-settlement-currency.

```text
unrealized PnL = signed quantity × (mark price - average entry price)
position notional = abs(quantity) × mark price
equity = settlement cash + unrealized PnL
initial margin = position notional × initial margin rate
maintenance margin = position notional × maintenance margin rate
liquidation fee reserve = position notional × liquidation fee rate
margin excess = equity - maintenance margin - liquidation fee reserve
```

An open position is liquidatable when:

```text
margin excess <= 0
```

`available_equity` is reported separately as equity minus initial margin.

## Forced liquidation

A liquidation request first evaluates the account without mutating state. A healthy account is rejected with no appended events.

When the configured condition is met, the simulator appends:

1. the trigger mark,
2. a synthetic market close order,
3. order acceptance,
4. a full-position derivative-PnL fill, and
5. the configured liquidation fee through the fill cash accounting.

The trigger mark and execution price are explicit inputs. The reference model does not infer a venue liquidation price. Forced close bypasses ordinary volume-participation limits and closes the full position.

## Deliberate exclusions

The model does not implement:

- cross-margin or portfolio offsets,
- implicit FX conversion,
- tiered maintenance-margin tables,
- partial liquidation ladders,
- bankruptcy-price calculation,
- insurance funds,
- auto-deleveraging,
- exchange-specific liquidation queues, or
- non-unit contract multipliers.

Those features require additional versioned assumptions rather than silent approximation.
