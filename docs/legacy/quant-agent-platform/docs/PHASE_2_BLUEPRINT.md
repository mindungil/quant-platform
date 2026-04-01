# Phase 2 Blueprint

## Goal

Introduce the first autonomous decision path for crypto only, without opening live execution.

## Services To Add

### memory-service

Responsibilities:

- store episodes, rules, and knowledge
- semantic search by current signal context
- record decision records

Initial API:

- `POST /memory/search`
- `POST /memory/record`
- `GET /memory/{id}`

Initial storage:

- PostgreSQL + pgvector

### strategy-registry

Responsibilities:

- store candidate and active strategies
- expose active strategy per asset type
- manage lifecycle states

Initial API:

- `GET /strategies/active?asset_type=crypto`
- `POST /strategies`
- `PATCH /strategies/{id}/status`

### crypto-agent

Responsibilities:

- consume `signal.threshold.crossed.crypto`
- gather context
- retrieve memory
- select strategy
- produce decision record

Initial implementation boundary:

- no live order calls yet
- output reasoning and simulated action only

## Required Contracts Before Coding

1. `SignalEvaluationResponse` must be stable.
2. Decision record schema must be frozen.
3. Strategy status transitions must be explicit.

## Minimal End State

- one `ACTIVE` crypto strategy
- one memory search flow
- one agent loop that consumes threshold events and records decisions
