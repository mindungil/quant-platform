# Quant Agent Platform

Phase 1 bootstrap for the startup-club autonomous trading platform.

## What Is Included

- `market-data`: candle ingestion and validation boundary
- `feature-store`: indicator calculation and feature read API
- `signal-service`: score evaluation based on feature-store output
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
- `api-gateway`: aggregate product-facing API
- `frontend`: dashboard scaffold served locally
- `AGENT.md`: local implementation contract derived from the Notion documents
- `docs/`: roadmap, architecture contract, and phase breakdown
- `Makefile`: local install, test, and compile helpers

## What Is Not Included Yet

- Persistent storage wiring
- NATS event consumers and durable subscriptions
- External data pipeline
- Trading execution, risk, backtest, and portfolio phases
  - Production persistence, auth, and real exchange integration remain simplified

## Services

```text
services/
  market-data
  feature-store
  signal-service
  memory-service
  strategy-registry
  crypto-agent
```

## Local Run

1. Copy `.env.example` to `.env` if needed.
2. Start the stack:

```bash
docker compose up --build
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

## Notes

- Current repositories are in-memory to keep Phase 1 focused on contracts and flow.
- `feature-store` owns all indicator computation by design.
- `signal-service` reads calculated features and only performs score composition.
- NATS plumbing is implemented as optional runtime integration so the same services can run by API or event flow.
