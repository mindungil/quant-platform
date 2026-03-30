# Quant Execution Tracker

This document converts the Notion source of truth into an executable repository plan.

## Source Documents

- `창업동아리`
- `SYSTEM_OVERVIEW`
- `SERVICE_SPECS`
- `AGENT_DESIGN`
- `CODING_AGENT_GUIDE`

## Current Baseline

Repository: `https://github.com/mindungil/quant`

What exists now:

- all major Phase 1 to Phase 5 service directories are present
- most services expose basic FastAPI routes and tests
- local `docker-compose.yml` boots core infra containers
- agent, execution, gateway, and frontend layers are bootstrap implementations

What is still materially missing relative to Notion:

- JetStream durable event flow
- PostgreSQL, pgvector, Redis, and Timescale-backed repositories
- full JWT + `X-User-ID` propagation + RLS isolation
- Next.js frontend and realtime WebSocket bridge
- observability stack and production-grade runtime controls

## Gap Classification

### Tier 1: Architecture blockers

- persistent storage adapters for stateful services
- event-bus reliability upgrade from best-effort NATS to JetStream
- missing gateway/auth boundaries required for multi-user isolation
- missing external data inputs required by Notion signal scoring

### Tier 2: Product blockers

- frontend is still a FastAPI HTML page, not a Next.js application
- gateway does not yet bridge events to WebSocket clients
- strategy validation and shadow lifecycle are simplified

### Tier 3: Hardening blockers

- no structured metrics/logging stack
- no startup smoke tests or compose health automation
- no full real exchange adapter controls such as provider-backed rate limiting or circuit breaker persistence

## Delivery Order

The sequence below follows the Notion phase dependency graph but expands each phase into repository tasks.

## Large Plan

- Milestone 1 to 7 below are the repository-wide completion plan.
- The goal is a local-first but full-surface implementation of the Notion architecture, then progressive hardening.

### Milestone 0: Project control plane

Definition:

- keep CLI memory updated
- keep this tracker aligned with implementation
- maintain a single active milestone at a time

Tasks:

- [ ] update `/home/ubuntu/.codex/memories/quant-platform.md` at each architectural change
- [ ] record milestone completion in this file
- [ ] keep `README.md` aligned with actual repository state

### Milestone 1: Phase 1 productionization

Definition:

- Phase 1 exists today but remains bootstrap-grade

Tasks:

- [ ] replace in-memory candle and feature repositories with Timescale-backed adapters
- [ ] add Redis latest-feature cache
- [ ] preserve feature-store as the only indicator calculator
- [ ] add event idempotency keys and anomaly topic flow
- [ ] add service-level integration tests for market to feature to signal flow

### Milestone 2: Phase 2 agent foundations

Tasks:

- [ ] move memory-service to PostgreSQL + pgvector schema
- [ ] move strategy-registry to PostgreSQL schema with lifecycle transitions
- [ ] expand crypto-agent state from template loop to full gather/retrieve/select/check/execute/record flow
- [ ] store full Decision Record schema from Notion
- [ ] consume threshold events rather than relying only on direct HTTP entrypoints
Current status:

- `memory-service` now has user-scoped API behavior via `X-User-ID`
- `strategy-registry` now has user-scoped strategy selection with bootstrap fallback
- `llm-gateway` and `external-data-service` exist and are wired into signal and agent flows

### Milestone 3: Phase 3 execution safety

Tasks:

- [ ] make backtest worker asynchronous and publish completion events
- [x] implement credential encryption round-trip with runtime retrieval only
- [ ] add exchange adapter interfaces for Binance, Upbit, and Alpaca
- [x] add rate limiter and circuit breaker behavior
- [x] enforce risk approval on all non-shadow orders

### Milestone 4: Phase 4 coordination and state

Tasks:

- [ ] expand orchestrator into supervisor and conflict-prevention service
- [ ] complete ETF and stock agent market-hours behavior with exchange calendars
- [ ] persist portfolio state and fill application
- [ ] compute statistics and drift detection against backtest baselines

### Milestone 5: Phase 5 product surface

Tasks:

- [x] add `auth-service`
- [x] add `api-gateway` JWT verification and internal user propagation
- [x] implement WebSocket bridge for trading events
- [ ] replace FastAPI frontend with Next.js app router application
- [ ] render dashboard views for portfolio, signals, agent feed, and strategy management
Current status:

- gateway now proxies authenticated memory and strategy routes
- gateway now exposes public `/dashboard`, `/signals`, `/feed`, `/settings`, `/orders`, and `/ws`
- frontend now consumes the gateway dashboard and websocket bridge

### Milestone 6: Missing Notion services

Tasks:

- [x] add `external-data-service` for news, on-chain, fear and greed, and macro feeds
- [x] add `llm-gateway` for reasoning-text-only generation via LiteLLM

### Milestone 7: Hardening

Tasks:

- [ ] add Prometheus metrics, Grafana dashboards, and Loki-compatible logs
- [x] add compose smoke tests and dependency probes
- [x] add CI workflow for tests and linting

## Immediate Build Priority

## Small Plan

Current execution slice:

1. add missing documented services so the repo matches the Notion service map
2. wire those services into existing signal and agent flows
3. verify compile-time integrity and keep memory plus tracker current

The highest-value next implementation slice is:

1. Introduce a durable shared service contract for persistence and user scoping.
2. Complete gateway-side authenticated routing on top of the new `auth-service`.
3. Migrate `memory-service` and `strategy-registry` to real PostgreSQL repositories.
4. Expand the crypto-agent to write the full Decision Record and call the execution path through risk and order services.

This is the smallest sequence that starts closing the biggest gap between the current bootstrap and the Notion architecture.

## Definition Of Done

A milestone is complete only when:

- code exists in the repository
- routes and models match the source spec closely enough to be exercised
- tests exist for the main behavior
- compose wiring exists where relevant
- docs no longer describe missing code as implemented
