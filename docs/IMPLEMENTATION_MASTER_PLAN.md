# Implementation Master Plan

This repository targets the full autonomous multi-asset trading platform described in the Notion architecture set:

- `창업동아리`
- `SYSTEM_OVERVIEW`
- `SERVICE_SPECS`
- `AGENT_DESIGN`
- `CODING_AGENT_GUIDE`

The plan below is intentionally large and detailed. It is not a vague roadmap. It is the execution contract for turning the current repository into a complete platform.

## 1. Delivery Objective

Build a local-first, service-oriented trading platform with:

- validated market data ingestion
- centralized feature computation
- signal generation with threshold events
- memory retrieval and strategy selection
- per-asset agent workers
- safe execution path with risk and credential controls
- portfolio and statistics tracking
- orchestration and health supervision
- user-facing gateway and dashboard

## 2. Delivery Principles

- Implement in phase order, but keep forward contracts visible.
- Every phase must leave behind runnable code, not design-only placeholders.
- Every service must expose health checks and basic tests.
- Shared contracts must stabilize before downstream automation depends on them.
- Trading decisions must remain deterministic. LLM reasoning can be layered later, but not as a decision source.
- Backtests must remain off the real-time critical path.

## 3. Full System Work Breakdown

### Phase 1. Market -> Feature -> Signal

Services:

- `market-data`
- `feature-store`
- `signal-service`

Responsibilities:

- validate candle input
- mark anomalies
- compute indicators in one place only
- emit threshold events for asset groups

Completion checklist:

- REST ingestion path
- event-driven path
- feature history path
- threshold publication path
- test coverage for validation and scoring

### Phase 2. Memory -> Strategy -> Crypto Agent

Services:

- `memory-service`
- `strategy-registry`
- `crypto-agent`

Responsibilities:

- record and search decision history
- manage strategy lifecycle
- perform first closed-loop autonomous decisions for crypto

Completion checklist:

- memory record/search APIs
- active strategy query path
- crypto decision loop
- decision record persistence
- threshold event consumption

### Phase 3. Safety And Execution

Services:

- `backtest-service`
- `exchange-adapter`
- `order-service`
- `risk-service`
- `credential-store`

Responsibilities:

- validate strategies asynchronously
- enforce risk limits before execution
- encrypt user exchange credentials
- abstract exchange execution
- separate shadow mode and real mode

Completion checklist:

- backtest scoring contract
- credential encryption round-trip
- risk approval API
- order execution API
- exchange adapter stub contract
- tests for approval and execution flow

### Phase 4. Coordination And State

Services:

- `orchestrator-agent`
- `etf-agent`
- `stock-agent`
- `portfolio-service`
- `statistics-service`

Responsibilities:

- coordinate cross-agent state
- respect trading-hour boundaries for non-crypto assets
- maintain portfolio position state
- compute operational and strategy metrics
- detect drift conditions

Completion checklist:

- orchestrator summary/health path
- portfolio fill application path
- statistics computation path
- ETF and stock market-hours guards

### Phase 5. Product Surface

Services:

- `api-gateway`
- `frontend`

Responsibilities:

- aggregate product-facing API
- expose realtime topic contract
- render a dashboard shell for the system

Completion checklist:

- gateway summary contract
- frontend service with usable dashboard shell
- local compose accessibility

## 4. Cross-Cutting Tracks

### Track A. Persistence Upgrade

Current local development uses in-memory repositories for speed. A production-complete path must replace those with:

- PostgreSQL + pgvector for memory and strategy data
- TimescaleDB for market and feature history
- Redis for latest feature cache and transient state

Migration tasks:

1. Replace service-local repositories with DB adapters.
2. Add schema migration tooling.
3. Add repository-level tests against real containers.

### Track B. Event Reliability

Current event flow is best-effort.

Upgrade tasks:

1. Move from plain NATS pub/sub to JetStream durable consumers.
2. Add delivery idempotency keys.
3. Add dead-letter subjects and replay tooling.

### Track C. Security

Current local stack is bootstrap-level.

Upgrade tasks:

1. Add JWT-based user identification.
2. Inject `X-User-ID` from gateway to internal services.
3. Add row-level isolation to stateful stores.
4. Prevent sensitive values from appearing in logs.

### Track D. Agent Intelligence

Current agent reasoning is deterministic and template-based.

Upgrade tasks:

1. Add decision explanation generation.
2. Add strategy switching based on registry and memory ranking.
3. Add multi-agent coordination constraints to avoid portfolio conflicts.

### Track E. Operations

Upgrade tasks:

1. Add Prometheus metrics per service.
2. Add structured logs.
3. Add startup dependency probes.
4. Add smoke tests for compose stack startup.

## 5. Dependency Order

The following dependency chain must be respected:

1. `market-data` before `feature-store`
2. `feature-store` before `signal-service`
3. `signal-service` before agents
4. `memory-service` and `strategy-registry` before autonomous agents
5. `risk-service`, `credential-store`, and `exchange-adapter` before `order-service`
6. `order-service` and fills before `portfolio-service`
7. `portfolio-service` plus outcomes before `statistics-service`
8. all service contracts before `api-gateway`
9. `api-gateway` before fully useful frontend

## 6. Repository Delivery Strategy

Commits should group work by milestone, not by random file batches.

Intended commit sequence:

1. bootstrap Phase 1 services
2. add Phase 2 agent loop services
3. add Phase 3 execution safety path
4. add Phase 4 coordination/state services
5. add Phase 5 gateway/frontend surface
6. stabilize tests and compose stack

## 7. Definition Of “Implemented”

A phase is counted as implemented only if:

- code exists in its service directory
- API routes exist
- core models exist
- at least one test validates the service behavior
- the service is wired into the repository workflow

## 8. Remaining Gap To Production Completeness

The platform will still require substantial work beyond the current repository baseline:

- real exchange integrations
- persistent storage replacement
- proper auth and tenancy
- websocket bridge and frontend realtime updates
- live backtest workers and strategy promotion jobs
- circuit breaker and rate limiting enforcement
- operational metrics and CI/CD

That gap is acceptable in the repository as long as the codebase already contains the full service map, stable contracts, and executable local foundations for every phase.
