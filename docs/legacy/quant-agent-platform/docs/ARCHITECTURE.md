# Architecture

## Phase 1 Runtime Topology

```text
market-data
  validates candle input
  publishes market.candle.updated.{asset}

feature-store
  accepts candles by API
  optionally consumes market.candle.updated.*
  computes indicators
  publishes feature.updated.{asset}

signal-service
  accepts manual evaluations by API
  optionally consumes feature.updated.*
  computes signal score
  stores latest evaluation

## Phase 2 Runtime Topology

```text
memory-service
  stores episodes and decision records
  supports lightweight search

strategy-registry
  stores strategy lifecycle state
  exposes active strategy by asset type

crypto-agent
  consumes signal.threshold.crossed.crypto
  fetches latest signal context
  searches memory
  loads active crypto strategy
  emits decision record and stores it in memory-service
```
```

## Subject Contract

- `market.candle.updated.{asset}`
  - source: `market-data`
  - payload: candle plus anomaly metadata
- `feature.updated.{asset}`
  - source: `feature-store`
  - payload: latest feature snapshot
- `signal.threshold.crossed.{asset_type}`
  - source: `signal-service`
  - payload: signal evaluation when threshold is crossed
- `agent.crypto.decision`
  - source: `crypto-agent`
  - payload: crypto decision record

## Deliberate Constraints

- No backtest calls in Phase 1.
- No LLM calls in Phase 1.
- No direct DB access across services.
- No strategy selection in Phase 1.

## Phase 2 Entry Conditions

- Feature contracts stable.
- Signal contract stable.
- NATS subject names stable.
- At least one end-to-end local flow verified.
