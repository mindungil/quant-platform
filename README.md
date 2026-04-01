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
- `quant-agent-platform` is now archived as legacy reference only; `quant` is the single active repository

## What Is Not Included Yet

- JetStream rollout for the full downstream execution and fill graph
- Full provider-complete live exchange connectivity beyond the current runnable local adapters
- Prometheus/Grafana-grade metrics across every service
- full event-backed replay coverage for every product event type and every service

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
docker-compose up --build
```

3. Bootstrap the admin and run a seeded operator flow:

```bash
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
curl -X POST http://localhost:8002/events/candles/BTCUSDT \
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

## Notes

- The product UI now lives in `services/frontend` as a Next.js app, while Grafana remains an internal ops concern.
- Compose is now intended to run with `STRICT_RUNTIME=true`; the critical stateful services should fail fast when backing dependencies are unavailable.
- `feature-store` owns all indicator computation by design.
- `signal-service` reads calculated features and composes signal plus external context.
- Gateway routes expose product-facing REST and websocket surfaces around the service mesh.
- JetStream is currently rolled out for the market, feature, signal, and crypto-agent path first.
- The first crypto release is operator-oriented and Binance-first. Live trading remains admin-gated and off by default.
- `quant` is now the only live repository. `quant-agent-platform` has been archived under `docs/legacy/`.
- RBAC is intentionally simple for now: `user` and `admin`.
- The observability profile is Compose-first: `docker-compose --profile observability up -d prometheus grafana`
