# Quant Production Program

This document is the long-horizon execution program for turning the current local-production baseline into an operator-usable trading product. It is intentionally concrete enough to drive several days of uninterrupted implementation without re-planning each session.

## Program Goal

Ship a local-production-grade quant platform in `/home/ubuntu/quant` that a single operator can run, monitor, and use through the gateway and product UI without relying on bootstrap-only in-memory behavior.

## Program Rules

- Every tranche must leave the repository runnable through `docker-compose`.
- Every major architectural change must update:
  - `README.md`
  - `docs/EXECUTION_TRACKER.md`
  - `/home/ubuntu/.codex/memories/quant-platform.md`
- Every feature tranche lands on `feature/platform-productionization` first.
- Commits stay vertical:
  - infra
  - data
  - execution/state
  - product/realtime
  - observability/release

## Git Flow Delivery Strategy

- `main`
  - release-ready only
- `develop`
  - integration branch for the next tagged release
- `feature/platform-productionization`
  - long-running productionization branch feeding `develop`
- `release/<version>`
  - cut after a green integration checkpoint and used for release-only fixes
- `hotfix/<issue>`
  - cut from `main` for urgent production defects only

### Commit Train

1. `feat(infra): add shared persistence jetstream and migration scaffolding`
2. `feat(data): persist market feature and signal pipeline`
3. `feat(memory): migrate memory and strategy stores`
4. `feat(platform): add durable data path and nextjs frontend`
5. `feat(state): persist execution state and realtime event bus`
6. `feat(observability): add metrics logging dashboards and smoke verification`
7. `docs(release): lock runtime and operator runbooks`

## Release Trains

### Train A: Durable Core Data

Outcome:

- market, feature, signal, memory, and strategy layers use real persistence
- Next.js frontend exists and builds in CI

Status:

- in place

### Train B: Execution State and Realtime

Outcome:

- orders, portfolio state, and statistics survive process restarts
- gateway websocket is fed by replayable service events instead of dashboard polling
- product UI can reflect real fill, signal, and agent activity

Scope:

- order-service persistent order ledger
- portfolio-service positions and fills tables
- statistics-service trade history and derived snapshot persistence
- Redis-backed realtime fanout and replay buffer
- gateway websocket consumer rewrite

Acceptance gate:

- order placement persists durable order, portfolio, and statistics artifacts
- gateway websocket replays recent events after reconnect
- `make test`, frontend build, and compose config all pass

### Train C: Agent Completion

Outcome:

- crypto-agent follows gather, retrieve, select, check, execute, record
- ETF and stock agents respect market calendars and share the same decision contract
- decision feed is a first-class product artifact

Scope:

- risk-aware execution from agent loop
- richer decision records
- agent scheduling and cross-asset coordination

### Train D: Operator Hardening

Outcome:

- stack exposes metrics, structured logs, health checks, smoke verification, and release runbooks

Scope:

- Prometheus metrics
- Grafana dashboards
- correlation IDs and JSON logs
- smoke commands and seed flows

### Train E: Release Readiness

Outcome:

- tagged release candidate can be promoted from `develop` to `main`

Scope:

- release checklist
- migration rollback notes
- operator documentation

## Current Tranche: Train B

### Immediate objectives

1. Persist `order-service` responses and state transitions in PostgreSQL.
2. Persist `portfolio-service` positions and fills in PostgreSQL.
3. Persist `statistics-service` trade history and computed snapshots in PostgreSQL.
4. Publish replayable realtime events from feature, signal, crypto-agent, and order flows into Redis.
5. Replace gateway polling websocket with Redis-backed replay + live subscribe delivery.

### Verification standard

- `python3 -m compileall /home/ubuntu/quant`
- `docker-compose -f docker-compose.yml config`
- `make test`
- frontend websocket reconnect still works against `/ws?token=...`

## Remaining Production Gaps After Train B

- full JetStream rollout beyond the data-to-agent path
- durable execution stores for exchange-side fills and risk incidents
- observability stack and operator dashboards
- release automation and smoke seed scenarios
