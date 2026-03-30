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
- `frontend`: dashboard surface for gateway-backed summary and live stream inspection
- `AGENT.md`: local implementation contract derived from the Notion documents
- `docs/`: roadmap, architecture contract, and phase breakdown
- `Makefile`: local install, test, and compile helpers

## What Is Not Included Yet

- Full persistent storage migration for every stateful service
- JetStream durable consumer upgrade across the whole event graph
- Real frontend migration to Next.js App Router product UI
- Full live exchange provider integrations beyond the local live-ready adapter contracts

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
curl -X POST http://localhost:8003/signals/evaluate/BTCUSDT
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
```

## Notes

- Several services are still in-memory internally, but the public contracts now model user-scoped auth, settings, orders, portfolio, statistics, and gateway aggregation.
- `feature-store` owns all indicator computation by design.
- `signal-service` reads calculated features and composes signal plus external context.
- Gateway routes now expose product-facing REST and websocket surfaces around the service mesh.
