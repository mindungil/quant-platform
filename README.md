# Quant Platform

Docker Compose productionization runtime for the startup-club autonomous trading platform.

## What Is Included

- `market-data`: candle ingestion and validation boundary
- `feature-store`: indicator calculation and feature read API
- `signal-service`: score evaluation based on feature-store output
- `external-data-service`: bootstrap news, macro, and on-chain context feed
- `memory-service`: episode and decision record store
- `strategy-registry`: strategy lifecycle and active strategy selection
- `crypto-agent`: first autonomous decision worker for crypto
- `backtest-service`: asynchronous-style strategy validation API
- `credential-store`: encrypted exchange credential storage
- `risk-service`: pre-trade approval and drawdown checks
- `exchange-adapter`: exchange abstraction with shadowable execution response
- `order-service`: order path through risk and adapter
- `portfolio-service`: holdings and order fill state
- `statistics-service`: aggregate metrics and drift signals
- `orchestrator-agent`: cross-service health and coordination summary
- `etf-agent`, `stock-agent`: non-crypto agent stubs with trading-hour guards
- `auth-service`: user registration, login, refresh, bootstrap admin, and RBAC-backed profile boundary
- `llm-gateway`: reasoning-text gateway for agent explanations
- `api-gateway`: aggregate product-facing API with authenticated proxy routes, admin RBAC, and a Redis-backed WebSocket replay bridge
- `frontend`: Next.js App Router product UI for dashboard, signals, feed, strategies, settings, and admin surfaces
- `AGENT.md`: local implementation contract derived from the Notion documents
- `docs/`: roadmap, architecture contract, and phase breakdown
- `docs/PRODUCTION_PROGRAM.md`: long-horizon productionization program and release-train plan
- `Makefile`: local install, test, and compile helpers
- `docs/legacy/quant-agent-platform/`: archived reference snapshot from the retired bootstrap repo

## Production Progress

- Shared SQLAlchemy, Redis, and JetStream-oriented primitives now live under `shared/`
- PostgreSQL and Timescale bootstrap artifacts now live under `migrations/` and `infra/`
- `market-data -> feature-store -> signal-service -> crypto-agent` now has durable event scaffolding with idempotency and DLQ support
- `memory-service` and `strategy-registry` now write through durable repositories with local fallback behavior
- `frontend` is now a Next.js application backed by the gateway public routes
- `order-service`, `portfolio-service`, and `statistics-service` now persist execution-state scaffolding through PostgreSQL-backed repositories
- gateway websocket now replays Redis-backed recent events instead of polling dashboard snapshots
- bootstrap admin, gateway RBAC, and admin operator routes now exist for `user` and `admin`
- Docker Compose now includes service healthchecks plus operator commands for `seed-admin`, `demo-flow`, and `smoke-e2e`
- global execution config now exists behind admin-only controls for `live_trading_enabled`, `allowed_exchanges`, `default_shadow_mode`, and `strict_runtime`
- `risk-service`, `exchange-adapter`, and `orchestrator-agent` now persist durable incidents, audit logs, and coordination snapshots
- `order-service` now records lifecycle history instead of only a last-state order row
- the crypto path now emits downstream execution events for `agent.crypto.action`, `order.created`, `order.filled`, `risk.triggered`, `portfolio.updated`, and `statistics.updated`
- gateway now propagates request and correlation IDs downstream
- crypto-critical services now expose shared request counters, latency histograms, inflight gauges, and JSON request logs with propagated correlation IDs
- core runtime health endpoints now validate backing dependencies instead of returning process-only success
- Prometheus now scrapes the crypto-critical mesh and Grafana ships with provisioned dashboards
- `quant-agent-platform` is now archived as legacy reference only; `quant` is the single active repository
- `crypto-agent` now implements the full 6-phase decision loop (gather/select/retrieve/check/execute/record) with per-phase timing
- `exchange-adapter` now has an abstract adapter layer with Binance (HMAC, rate limiter), Upbit, and Alpaca stubs
- `backtest-service` now supports async job execution with polling and completion events
- ETF and stock agents now have market-hours-guarded decision endpoints with exchange calendars (KR/US holidays)
- `orchestrator-agent` now performs real downstream health checks and cross-agent conflict detection
- Full integration test suite covers the market→feature→signal→agent→order chain (12 tests)
- Domain-level Prometheus metrics now cover risk denials, drawdown breaches, order fills/lifecycle, strategy drift score/alerts, agent decision phases/outcomes, and JetStream consumer health
- Event reliability integration tests cover duplicate delivery idempotency, event replay with past timestamps, and DLQ envelope handling (14 tests)
- Strategy analysis page with backtest baseline vs live performance comparison and drift indicator badges (red/yellow/green)
- Settings page now includes execution config panel (live_trading_enabled, shadow_mode, allowed_exchanges)
- Reusable ErrorBoundary, EmptyState, and LoadingSkeleton components for improved UX polish
- Binance collector publishes `market.candle.ingested` NATS events with Prometheus `candle_ingest_total` metrics
- Signal staleness enforcement in crypto-agent with `stale_signal_skipped_total` counter; all silent `pass` exception blocks replaced with structured logging
- Orchestrator `/pipeline/health` endpoint checks the full signal chain: market-data → feature-store → signal-service → crypto-agent
- Outcome consumer hardened with 3-attempt retry, `outcome_reinforcement_total`/`skipped`/`pnl` metrics, and `memory.reinforce.failed` failure events
- Backtest auto-promotion: strategies auto-transition (PENDING→TESTED→SHADOW) based on Sharpe thresholds; `POST /strategies/backtest-callback` endpoint for external triggers
- Shadow strategy tracker subscribes to `order.filled` and maintains running shadow metrics (Sharpe, win_rate, drawdown) per strategy
- Drift detection → auto-deprecation: statistics-service publishes `strategy.drift_alert`; strategy-registry consumes it and transitions ACTIVE → DEPRECATED on critical drift

## What Is Not Included Yet

- Full provider-complete live exchange connectivity beyond the current Binance adapter (Upbit and Alpaca are stubs)
- Deeper RLS-style row isolation beyond current user-scoped API behavior

## Services

```text
services/
  market-data
  feature-store
  signal-service
  external-data-service
  memory-service
  strategy-registry
  crypto-agent
  auth-service
  api-gateway
  frontend
  order-service
  portfolio-service
  statistics-service
```

## Local Run

1. Copy `.env.example` to `.env` if you want a local override file.
2. Start the stack:

```bash
docker-compose up -d --build
```

3. Bootstrap the admin and run a seeded operator flow:

```bash
make compose-up
make seed-admin
make demo-flow
make smoke-e2e
make release-check
```

4. Manual example flow:

```bash
curl -X POST http://localhost:8001/candles/BTCUSDT \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-03-30T00:00:00Z",
    "open": 82000,
    "high": 82300,
    "low": 81800,
    "close": 82150,
    "volume": 1200
  }'
```

```bash
curl http://localhost:8002/features/BTCUSDT/latest
curl "http://localhost:8002/features/BTCUSDT/history?from_ts=2026-03-26T00:00:00Z&to_ts=2026-03-30T23:59:59Z"
curl -X POST http://localhost:8003/signals/evaluate/BTCUSDT
curl "http://localhost:8003/signals/BTCUSDT/history?from_ts=2026-03-26T00:00:00Z&to_ts=2026-03-30T23:59:59Z"
curl http://localhost:8005/strategies/active?asset_type=crypto
curl -X POST http://localhost:8006/decisions/run/BTCUSDT
```

5. Auth and gateway example flow:

```bash
curl -X POST http://localhost:8017/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"password123","display_name":"Demo","plan":"premium"}'
```

```bash
curl -X POST http://localhost:8017/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@example.com","password":"password123"}'
```

Use the returned `access_token` against:

```bash
curl http://localhost:8017/dashboard -H "Authorization: Bearer <token>"
curl http://localhost:8017/signals -H "Authorization: Bearer <token>"
curl http://localhost:8017/feed -H "Authorization: Bearer <token>"
```

6. Product UI:

```bash
open http://localhost:8018
```

Admin UI is available after admin login at:

```bash
open http://localhost:8018/admin
```

## Deployment Architecture

The stack runs in **5 containers** (down from 25):

| Container | Description | Port(s) |
|-----------|-------------|---------|
| `backend` | All 20 Python microservices (separate uvicorn processes) | 8001-8021 |
| `frontend` | Next.js product UI | 8018 |
| `db` | TimescaleDB + pgvector (single database server) | 5432 |
| `redis` | Cache + realtime pub/sub | 6379 |
| `nats` | JetStream event bus | 4222 |

Optional: `docker-compose --profile observability up -d` adds Prometheus (9090) and Grafana (3001).

## Notes

- All backend services share one container but run as isolated processes with separate ports.
- Services communicate via `localhost` within the backend container.
- `feature-store` owns all indicator computation by design.
- `signal-service` reads calculated features and composes signal plus external context.
- Gateway (port 8017) is the product-facing entry point for REST and WebSocket.
- JetStream spans the full crypto execution graph from signal thresholding through order/portfolio/statistics events.
- Live trading is admin-gated and off by default. The first crypto release is Binance-first.
- RBAC is intentionally simple for now: `user` and `admin`.
