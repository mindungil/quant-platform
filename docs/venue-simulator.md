# Deterministic Venue Matching

`DeterministicVenueSimulator` converts validated venue orders and frozen quote evidence into append-only execution-engine events.

## Versioned inputs

Each simulator instance is bound to one immutable `ExecutionProfileSnapshot`. Every accepted or rejected order records the profile snapshot ID, and every fill records:

- profile snapshot ID,
- participation-model version,
- quote ID and observation time,
- available quantity and the applied participation cap,
- maker or taker classification,
- fill quantity and price,
- charged fee or signed rebate cash flow.

The profile must be effective at order submission, and the order, quote, and profile symbols must match.

## Order constraints

Before acceptance, the simulator applies the existing symbol rules without silently rounding the requested order:

- minimum and maximum quantity,
- quantity step or market-lot override,
- limit-price tick and bounds,
- minimum and maximum notional,
- contract multiplier.

An invalid order is submitted and rejected in the event log with explicit violation codes. It never reaches the matching path.

## Latency

`order_latency` shifts venue acceptance from client submission time to venue-arrival time. A quote observed before arrival cannot fill the order. Events with the same timestamp remain ordered by the execution engine's contiguous sequence number.

## Taker matching

- Market BUY uses the quote ask and ask quantity.
- Market SELL uses the quote bid and bid quantity.
- A BUY limit at or above the ask crosses as taker.
- A SELL limit at or below the bid crosses as taker.

## Maker matching

A non-crossing limit remains open until trade evidence reaches its price:

- passive BUY fills only when trade price is at or below the limit;
- passive SELL fills only when trade price is at or above the limit.

The reference model fills at the submitted limit price. This is intentionally conservative and does not infer queue position or price improvement.

## Volume participation and partial fills

The maximum quantity available to one match is:

```text
available_quantity × max_volume_participation
```

The result is floored to the applicable quantity increment, then capped by remaining order quantity. Repeated quotes may therefore produce multiple partial fills before the order becomes filled.

For taker fills, available quantity comes from the matching bid or ask. For maker fills, it comes from reported trade volume.

## Fees and rebates

Maker and taker rates come from the same `ExecutionProfileSnapshot` used for order rules.

- Positive rates become fill fees.
- Negative maker rates become separate positive cash events so the fill schema never represents a negative fee.

## Cancel and replace

Cancellation is an explicit terminal event. Replacement is modeled as:

1. cancel the existing active order;
2. submit a new order with a new ID and `replaces_order_id` pointing to the cancelled order.

No order history is mutated.

## Deliberate limitations

This first venue layer does not model:

- order-book queue position,
- hidden or iceberg liquidity,
- stochastic latency,
- intrabar quote interpolation,
- borrow or margin interest,
- funding schedules,
- collateral and maintenance margin,
- liquidation.

Those are separate layers over the same event engine and versioned venue profile.