# Roadmap

This roadmap turns the Notion plan into executable repository milestones.

## Track A: Delivery Sequence

1. Phase 1 foundation
   - `market-data` validation boundary
   - `feature-store` indicator authority
   - `signal-service` score authority
   - NATS subjects and local orchestration
2. Phase 2 intelligence
   - `memory-service`
   - `strategy-registry`
   - `crypto-agent`
3. Phase 3 execution safety
   - `backtest-service`
   - `exchange-adapter`
   - `order-service`
   - `risk-service`
   - `credential-store`
4. Phase 4 coordination
   - `orchestrator-agent`
   - `etf-agent`
   - `stock-agent`
   - `portfolio-service`
   - `statistics-service`
5. Phase 5 productization
   - frontend dashboard
   - gateway WebSocket bridge
   - operator observability

## Track B: Cross-Cutting Workstreams

- Architecture contracts
  - subject naming
  - request and response schemas
  - env var conventions
- Reliability
  - health checks
  - idempotent event handling
  - retries and dead-letter strategy
- Data quality
  - candle validation
  - anomaly marking
  - missing interval strategy
- Security
  - service auth later via gateway
  - encrypted credentials in Phase 3
- Operability
  - local dev flow
  - test entrypoints
  - compose stack

## Immediate Execution Window

The current repository should complete these concrete items first:

1. Make Phase 1 services executable locally.
2. Replace manual-only flow with event-aware plumbing.
3. Keep storage in-memory while contracts stabilize.
4. Lock down the next service boundaries in docs before Phase 2 coding starts.
