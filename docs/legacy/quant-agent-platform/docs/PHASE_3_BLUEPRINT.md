# Phase 3 Blueprint

## Goal

Open a safe execution path with asynchronous validation and risk gates.

## Services To Add

### backtest-service

Responsibilities:

- evaluate pending strategies asynchronously
- publish completion events
- never block live decision flow

### exchange-adapter

Responsibilities:

- standardize exchange calls
- enforce retry, rate limit, and circuit breaker
- fetch credentials at execution time only

Required interface:

- `place_order`
- `cancel_order`
- `get_balance`
- `get_positions`
- `get_orderbook`

### order-service

Responsibilities:

- receive approved action requests
- route shadow vs real execution
- persist order lifecycle status

### risk-service

Responsibilities:

- pre-trade approval
- drawdown and exposure enforcement
- halt publication on critical breach

### credential-store

Responsibilities:

- encrypted API key storage
- runtime retrieval only
- no plain-text persistence

## Phase 3 Non-Negotiables

- shadow mode before real execution
- async backtests only
- exchange access only through adapter
- encrypted credentials only
- risk approval required before real order placement
