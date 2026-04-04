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

- all major Phase 1 to Phase 5 service directories are present and productionized
- most services have durable PostgreSQL/TimescaleDB storage with Redis caching
- JetStream event bus spans the full crypto execution graph
- crypto-agent implements the full 6-phase decision loop (gather/select/retrieve/check/execute/record)
- exchange-adapter has abstract adapter layer with Binance implementation + Upbit/Alpaca stubs
- backtest-service supports async job execution with polling
- ETF and stock agents have market-hours-guarded decision endpoints with exchange calendars
- orchestrator-agent performs real health checks and cross-agent conflict detection
- full integration test suite covers the market→feature→signal→agent→order chain
- Docker Compose boots with healthchecks, Prometheus/Grafana observability profile

What remains for full Notion parity:

- richer business-level metrics for fills, risk denials, and strategy performance
- deeper RLS-style isolation and signed internal trust expansion
- full provider-complete live exchange connectivity beyond current Binance adapter

## Gap Classification

### Tier 1: Architecture blockers — RESOLVED

- [x] persistent storage adapters for stateful services
- [x] event-bus reliability upgrade from best-effort NATS to JetStream
- [x] missing gateway/auth boundaries required for multi-user isolation
- [x] missing external data inputs required by Notion signal scoring

### Tier 2: Product blockers — RESOLVED

- [x] frontend product UI via Next.js
- [x] websocket replay with Redis-backed delivery
- [x] strategy validation via async backtest jobs
- [x] settings and strategy UX depth improvements

### Tier 3: Hardening blockers

- [x] shared request metrics and structured JSON logs
- [x] Prometheus/Grafana observability profile
- [x] richer domain-level metrics for risk, fills, strategy drift, and JetStream consumer health
- [x] duplicate-delivery and replay-path integration coverage

## Delivery Order

The sequence below follows the Notion phase dependency graph but expands each phase into repository tasks.

## Large Plan

### Milestone 0: Project control plane

Tasks:

- [x] update CLI memory at each architectural change
- [x] record milestone completion in this file
- [x] keep `README.md` aligned with actual repository state
- [x] retire `quant-agent-platform` as an active workspace and archive it under `docs/legacy/`

### Milestone 1: Phase 1 productionization

Tasks:

- [x] replace in-memory candle and feature repositories with Timescale-backed adapters
- [x] add Redis latest-feature cache
- [x] preserve feature-store as the only indicator calculator
- [x] add event idempotency keys and anomaly topic flow
- [x] add service-level integration tests for market to feature to signal flow

### Milestone 2: Phase 2 agent foundations

Tasks:

- [x] move memory-service to PostgreSQL + pgvector schema
- [x] move strategy-registry to PostgreSQL schema with lifecycle transitions
- [x] expand crypto-agent to full gather/select/retrieve/check/execute/record flow
- [x] store full Decision Record schema from Notion
- [x] consume threshold events rather than relying only on direct HTTP entrypoints
- [x] publish deterministic `agent.crypto.action` events with correlated order intent
- [x] add per-phase timing and status tracking to decision records

### Milestone 3: Phase 3 execution safety

Tasks:

- [x] make backtest worker asynchronous and publish completion events
- [x] implement credential encryption round-trip with runtime retrieval only
- [x] add exchange adapter interfaces for Binance, Upbit, and Alpaca
- [x] add rate limiter and circuit breaker behavior
- [x] enforce risk approval on all non-shadow orders
- [x] persist risk incidents and query them durably
- [x] persist exchange audit trail for operator inspection
- [x] add global admin execution config with live-trading gate defaults
- [x] emit downstream execution events for orders, risk denials, portfolio updates, and statistics updates

### Milestone 4: Phase 4 coordination and state

Tasks:

- [x] expand orchestrator into health aggregator with conflict detection
- [x] persist orchestrator coordination snapshots
- [x] complete ETF and stock agent market-hours behavior with exchange calendars
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
- [x] add full crypto execution flow integration tests
- [x] add richer domain metrics for risk, fills, strategy drift, and JetStream consumer health
- [x] add duplicate-delivery and replay-path integration tests
- [x] add settings and strategy UX depth improvements

## Definition Of Done

A milestone is complete only when:

- code exists in the repository
- routes and models match the source spec closely enough to be exercised
- tests exist for the main behavior
- compose wiring exists where relevant
- docs no longer describe missing code as implemented
