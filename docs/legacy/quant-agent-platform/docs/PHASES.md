# Phases

## Phase 1

Goal: market data to signal generation.

Exit criteria:

- validated candle ingestion
- indicator calculation only in `feature-store`
- score evaluation only in `signal-service`
- local compose stack available
- tests for validator and scoring

## Phase 2

Goal: first asset-specific autonomous decision layer.

Planned services:

- `memory-service`
- `strategy-registry`
- `crypto-agent`

Expected outputs:

- memory search contract
- strategy lifecycle contract
- LangGraph crypto agent state machine

## Phase 3

Goal: safe execution path.

Planned services:

- `backtest-service`
- `exchange-adapter`
- `order-service`
- `risk-service`
- `credential-store`

Expected outputs:

- async strategy validation
- runtime credential retrieval
- shadow mode ordering path

## Phase 4

Goal: multi-agent coordination and portfolio state.

Planned services:

- `orchestrator-agent`
- `etf-agent`
- `stock-agent`
- `portfolio-service`
- `statistics-service`

## Phase 5

Goal: user-facing product and operator tooling.

Planned services:

- frontend dashboard
- api-gateway WebSocket bridge
- observability stack integration
