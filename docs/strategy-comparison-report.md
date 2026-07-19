# Strategy Comparison Report

`StrategyComparisonReport` turns an explicitly selected Research Workbench result into a reproducible comparison artifact before Validation begins.

## Common comparison context

Every report is bound to one immutable:

- Exploration report SHA-256,
- dataset ID tuple,
- code revision,
- cost-scenario ID and SHA-256, and
- benchmark ID and revision.

These values live at the report level rather than the candidate level, so candidates cannot silently use different data, costs, or benchmark assumptions.

## Economic hypotheses and search disclosure

The v1 report requires at least three candidates with distinct economic-hypothesis descriptions. Each candidate identifies its successful Exploration attempt and discloses:

- strategy and candidate IDs,
- the number of Exploration trials for that strategy,
- every searched value for each reported parameter, and
- the selected attempt's parameters within those disclosed values.

This distinguishes genuinely different economic ideas from repeated parameter variants of one idea.

## Performance waterfall

Candidate PnL is represented as:

```text
Economic Net PnL = Gross PnL - sum(cost lines)
Estimated after-tax PnL = Economic Net PnL - estimated tax
```

A cost amount is signed from the deduction perspective:

- a positive commission, spread, slippage, impact, interest, or tax line reduces Gross PnL,
- a negative rebate or financing credit increases Economic Net PnL.

The report never infers missing costs. An omitted line means no amount was supplied for that line, not that the real-world cost is known to be zero.

## Tax evidence

Each candidate records:

- tax-rule version,
- estimated tax,
- confidence (`confirmed`, `assumed`, or `review_required`), and
- a mandatory explanation when review is required.

The estimate is informational evidence, not a final tax filing result.

## Risk and implementation limits

Each candidate also exposes:

- OOS return,
- turnover,
- event concentration from 0 to 1,
- capacity-limit notional or an explicit not-estimated state, and
- comparison guards used to decide selection eligibility.

The selected candidate must pass every comparison guard. Other candidates remain in the report even when they are not selected.

## Selection and Validation

The source Exploration report must have exactly one currently selected attempt. The comparison report preserves that attempt and the written selection reason.

`bind_validation_plan()` accepts a Validation plan only when all of the following match:

- Exploration report SHA-256,
- selected attempt ID,
- dataset IDs, and
- code revision.

This prevents a non-selected candidate or a changed experiment from being attached to the selected candidate's Validation evidence.

## Output

The artifact provides:

- deterministic JSON,
- content SHA-256, and
- deterministic Markdown with a candidate table, cost waterfalls, search disclosure, tax confidence, and Validation-plan binding.

Identical input objects must produce identical JSON, Markdown, and digest bytes.

## Deliberate exclusions

This public contract does not:

- implement proprietary strategies,
- calculate strategy metrics from market data,
- choose the winning candidate automatically,
- estimate unprovided cost or capacity models,
- resolve `review_required` tax evidence, or
- open Holdout data.

Those responsibilities belong to the private strategy/evidence layer and the pre-committed Validation process.
