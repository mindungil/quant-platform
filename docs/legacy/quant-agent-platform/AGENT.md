# AGENT.md

This repository implements the startup-club trading platform described in Notion.

## Source Of Truth

- Startup-club page: `창업동아리`
- Architecture references:
  - `SYSTEM_OVERVIEW`
  - `SERVICE_SPECS`
  - `AGENT_DESIGN`
  - `CODING_AGENT_GUIDE`

## Mandatory Rules

- Build services in phase order only.
- Phase 1 scope is limited to `market-data`, `feature-store`, and `signal-service`.
- Service-to-service data access must go through APIs or events, never direct DB reads across services.
- `feature-store` is the single source of truth for indicator calculation.
- `signal-service` must read feature data, not calculate indicators directly.
- `pgvector` is the only vector-store choice for later phases. Do not add Qdrant.
- LLMs must not make trading decisions. Later agent layers may generate reasoning only.
- Real-time trade execution must never run synchronous backtests in the request path.

## Current Bootstrap Decisions

- This repository starts with a local bootstrap implementation for Phase 1.
- Persistence is in-memory first so service contracts can settle before infrastructure is added.
- NATS, Redis, PostgreSQL, and TimescaleDB are defined in local orchestration, but Phase 1 service code currently uses in-memory repositories.
- Indicator calculation is isolated behind `feature-store` so the engine can later be replaced with TA-Lib and pandas-ta without changing callers.

## Phase Plan

1. Phase 1: `market-data` -> `feature-store` -> `signal-service`
2. Phase 2: `memory-service` -> `strategy-registry` -> `crypto-agent`
3. Phase 3: `backtest-service` -> `exchange-adapter` -> `order-service` -> `risk-service` -> `credential-store`
4. Phase 4: `orchestrator-agent` -> `etf-agent` -> `stock-agent` -> `portfolio-service` -> `statistics-service`
5. Phase 5: frontend and WebSocket bridge

## Repository Convention

Each service follows:

```text
service-name/
  app/
    api/
    core/
    models/
    services/
    db/
  tests/
  Dockerfile
  requirements.txt
```
