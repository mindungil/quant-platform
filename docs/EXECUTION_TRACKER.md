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
- agent, execution, gateway, and frontend layers now have a mixed productionization baseline with durable scaffolding in place

What is still materially missing relative to Notion:

- full JetStream durable event flow across the entire graph
- durable storage migration for the remaining exchange-side and orchestration stateful services
- full JWT + `X-User-ID` propagation + RLS isolation
- observability stack and production-grade runtime controls

## Gap Classification

### Tier 1: Architecture blockers

- persistent storage adapters for stateful services
- event-bus reliability upgrade from best-effort NATS to JetStream
- missing gateway/auth boundaries required for multi-user isolation
- missing external data inputs required by Notion signal scoring

### Tier 2: Product blockers

- settings, signals, and feed pages need richer UX depth on top of the new Next.js surface
- websocket replay exists but still needs broader event coverage and stronger delivery guarantees
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

- [x] update `/home/ubuntu/.codex/memories/quant-platform.md` at each architectural change
- [ ] record milestone completion in this file
- [x] keep `README.md` aligned with actual repository state

### Milestone 1: Phase 1 productionization

Definition:

- Phase 1 exists today but remains bootstrap-grade

Tasks:

- [x] replace in-memory candle and feature repositories with Timescale-backed adapters
- [x] add Redis latest-feature cache
- [ ] preserve feature-store as the only indicator calculator
- [x] add event idempotency keys and anomaly topic flow
- [ ] add service-level integration tests for market to feature to signal flow

### Milestone 2: Phase 2 agent foundations

Tasks:

- [x] move memory-service to PostgreSQL + pgvector schema
- [x] move strategy-registry to PostgreSQL schema with lifecycle transitions
- [ ] expand crypto-agent state from template loop to full gather/retrieve/select/check/execute/record flow
- [x] store full Decision Record schema from Notion
- [x] consume threshold events rather than relying only on direct HTTP entrypoints
Current status:

- `memory-service` now has user-scoped API behavior via `X-User-ID`
- `strategy-registry` now has user-scoped strategy selection with PostgreSQL persistence plus bootstrap fallback
- `llm-gateway` and `external-data-service` exist and are wired into signal and agent flows
- `crypto-agent` now subscribes to `signal.threshold.crossed.crypto` through JetStream-oriented durable consumers

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
- [x] persist portfolio state and fill application
- [x] compute statistics and drift detection against backtest baselines

### Milestone 5: Phase 5 product surface

Tasks:

- [x] add `auth-service`
- [x] add `api-gateway` JWT verification and internal user propagation
- [x] implement WebSocket bridge for trading events
- [x] replace FastAPI frontend with Next.js app router application
- [x] render dashboard views for portfolio, signals, agent feed, strategy management, and settings
Current status:

- gateway now proxies authenticated memory and strategy routes
- gateway now exposes public `/dashboard`, `/signals`, `/feed`, `/settings`, `/orders`, and `/ws`
- frontend now consumes the gateway dashboard and websocket bridge through a Next.js App Router surface
- gateway websocket now replays Redis-backed recent events instead of rebuilding dashboard snapshots on a loop

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

1. harden integration coverage for the newly introduced durable execution adapters
2. extend realtime event coverage to every product-facing event source
3. layer observability and operational runbooks on top of the new runtime

The highest-value next implementation slice is:

1. Add integration tests for market -> feature -> signal -> crypto-agent JetStream flow.
2. Add durable exchange-side event capture and richer realtime fanout coverage.
3. Replace best-effort replay with stronger delivery semantics and operator introspection.
4. Add observability stack and release smoke commands.

This is the next smallest sequence that closes the remaining gap between the current local-production baseline and the Notion target architecture.

## Definition Of Done

A milestone is complete only when:

- code exists in the repository
- routes and models match the source spec closely enough to be exercised
- tests exist for the main behavior
- compose wiring exists where relevant
- docs no longer describe missing code as implemented
