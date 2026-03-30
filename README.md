# Quant Agent Platform

Productionizing local runtime for the startup-club autonomous trading platform.

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
- `auth-service`: bootstrap JWT issue/verify boundary for user propagation
- `llm-gateway`: reasoning-text gateway for agent explanations
- `api-gateway`: aggregate product-facing API with authenticated proxy routes and a WebSocket snapshot bridge
- `frontend`: Next.js App Router product UI for dashboard, signals, feed, strategies, and settings
- `AGENT.md`: local implementation contract derived from the Notion documents
- `docs/`: roadmap, architecture contract, and phase breakdown
- `Makefile`: local install, test, and compile helpers

## Production Progress

- Shared SQLAlchemy, Redis, and JetStream-oriented primitives now live under `shared/`
- PostgreSQL and Timescale bootstrap artifacts now live under `migrations/` and `infra/`
- `market-data -> feature-store -> signal-service -> crypto-agent` now has durable event scaffolding with idempotency and DLQ support
- `memory-service` and `strategy-registry` now write through durable repositories with local fallback behavior
- `frontend` is now a Next.js application backed by the gateway public routes

## What Is Not Included Yet

- Full durable migration for `order-service`, `portfolio-service`, and `statistics-service`
- JetStream rollout for the full downstream execution and fill graph
- Full provider-complete live exchange connectivity beyond the current runnable local adapters
- Prometheus/Grafana/Loki-grade observability across every service

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

1. Copy `.env.example` to `.env` if needed.
2. Start the stack:

```bash
docker-compose up --build
```

3. Example flow:

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

4. Auth and gateway example flow:

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

5. Product UI:

```bash
open http://localhost:8018
```

## Notes

- The product UI now lives in `services/frontend` as a Next.js app, while Grafana remains an internal ops concern.
- Several services still retain in-memory fallback behavior so local development does not hard-fail when Postgres, Timescale, or Redis are absent.
- `feature-store` owns all indicator computation by design.
- `signal-service` reads calculated features and composes signal plus external context.
- Gateway routes expose product-facing REST and websocket surfaces around the service mesh.
- JetStream is currently rolled out for the market, feature, signal, and crypto-agent path first.
