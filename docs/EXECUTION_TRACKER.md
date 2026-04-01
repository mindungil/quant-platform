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

- durable storage migration for the remaining exchange-side and orchestration stateful services
- deeper RLS-style isolation and signed internal trust expansion beyond the current gateway boundary
- richer domain metrics and dashboards beyond the shared request-level observability now in place
- stronger integration coverage for the full crypto execution graph and live-mode gating

## Gap Classification

### Tier 1: Architecture blockers

- persistent storage adapters for stateful services
- event-bus reliability upgrade from best-effort NATS to JetStream
- missing gateway/auth boundaries required for multi-user isolation
- missing external data inputs required by Notion signal scoring

### Tier 2: Product blockers

- settings, signals, and feed pages still need richer UX depth on top of the new Next.js surface
- websocket replay now exists with admin inspection, but still needs stronger delivery guarantees
- strategy validation and shadow lifecycle are simplified

### Tier 3: Hardening blockers

- business-level metrics and dashboards are still thinner than the request/logging layer now in place
- duplicate-delivery and replay-path integration coverage is still incomplete
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
- [x] retire `quant-agent-platform` as an active workspace and archive it under `docs/legacy/`

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
- [x] publish deterministic `agent.crypto.action` events with correlated order intent
Current status:

- `memory-service` now has user-scoped API behavior via `X-User-ID`
- `strategy-registry` now has user-scoped strategy selection with PostgreSQL persistence plus bootstrap fallback
- `llm-gateway` and `external-data-service` exist and are wired into signal and agent flows
- `crypto-agent` now subscribes to `signal.threshold.crossed.crypto` through JetStream-oriented durable consumers
- `crypto-agent` now persists decision records durably and emits correlated downstream action events

### Milestone 3: Phase 3 execution safety

Tasks:

- [ ] make backtest worker asynchronous and publish completion events
- [x] implement credential encryption round-trip with runtime retrieval only
- [ ] add exchange adapter interfaces for Binance, Upbit, and Alpaca
- [x] add rate limiter and circuit breaker behavior
- [x] enforce risk approval on all non-shadow orders
- [x] persist risk incidents and query them durably
- [x] persist exchange audit trail for operator inspection
- [x] add global admin execution config with live-trading gate defaults
- [x] emit downstream execution events for orders, risk denials, portfolio updates, and statistics updates

### Milestone 4: Phase 4 coordination and state

Tasks:

- [ ] expand orchestrator into supervisor and conflict-prevention service
- [x] persist orchestrator coordination snapshots
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
- [x] add admin bootstrap, RBAC, and operator UI surfaces
Current status:

- gateway now proxies authenticated memory and strategy routes
- gateway now exposes public `/dashboard`, `/signals`, `/feed`, `/settings`, `/orders`, and `/ws`
- frontend now consumes the gateway dashboard and websocket bridge through a Next.js App Router surface
- gateway websocket now replays Redis-backed recent events instead of rebuilding dashboard snapshots on a loop
- gateway now exposes `/admin/users`, `/admin/system/health`, and `/admin/system/events`
- frontend now includes `/admin`, `/admin/users`, and `/admin/system`

### Milestone 6: Missing Notion services

Tasks:

- [x] add `external-data-service` for news, on-chain, fear and greed, and macro feeds
- [x] add `llm-gateway` for reasoning-text-only generation via LiteLLM

### Milestone 7: Hardening

Tasks:

- [x] add Compose-first Prometheus and Grafana profile scaffolding
- [x] add Prometheus scrape coverage across the crypto-critical mesh
- [x] add shared request metrics and structured JSON logs across the crypto-critical mesh
- [x] add compose smoke tests and dependency probes
- [x] add CI workflow for tests and linting
- [ ] add richer domain metrics for risk, fills, strategy drift, and JetStream consumer health

## Immediate Build Priority

## Small Plan

Current execution slice:

1. harden operator flows and admin RBAC around the new runtime
2. complete the full JetStream-backed downstream crypto execution graph
3. add stronger integration coverage for duplicate-delivery, replay, and live-mode gating
4. deepen domain-level metrics beyond shared request/latency instrumentation

The highest-value next implementation slice is:

1. Add integration tests for market -> feature -> signal -> crypto-agent -> order -> portfolio/statistics JetStream flow.
2. Add duplicate-delivery and replay verification for downstream crypto consumers.
3. Expand service-specific domain metrics and dashboards for risk denials, fills, and decision latency.
4. Harden live-mode gating verification in `demo-flow`, `smoke-e2e`, and `release-check`.

This is the next smallest sequence that closes the remaining gap between the current local-production baseline and the Notion target architecture.

## Definition Of Done

A milestone is complete only when:

- code exists in the repository
- routes and models match the source spec closely enough to be exercised
- tests exist for the main behavior
- compose wiring exists where relevant
- docs no longer describe missing code as implemented
