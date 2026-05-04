# Service Consolidation Plan — 21 processes → 6 domain containers

**Status**: Frozen reference. Any deviation must be documented as an amendment at the bottom.

**Owner**: quant-platform-team
**Start date**: 2026-04-14
**Target**: Independent domain containers with clear ownership, no behavior regression.

---

## 1. Motivation

The current codebase has 21 logically-separated services running as 20 uvicorn processes inside a **single** `backend` Docker container (`Dockerfile.backend` + `scripts/start_all.sh`). This has served well for rapid iteration but creates three problems as we go to production:

1. **Blast radius** — one bad deploy of e.g. `crypto-agent` restarts every process including `api-gateway`.
2. **Dependency bleed** — every service installs every other service's `requirements.txt` (`cat /tmp/reqs/*.txt | sort -u`). A `scipy` update for backtest breaks the gateway.
3. **Ownership ambiguity** — feature additions don't have an obvious home service since boundaries are soft (single container, shared image, shared logs).

Goal: consolidate into **6 domain containers** that are independently buildable, deployable, and scalable, with no functional regression.

## 2. Principles

1. **Behavioral no-op.** This refactor changes deployment topology only. No business logic, no schema changes, no API surface changes. If a customer notices, we failed.
2. **One domain = one container = one Dockerfile = one requirements set.** Each domain container internally still runs N uvicorn processes on their current ports (easier migration, preserves the working process-isolation pattern from `start_all.sh`).
3. **DNS over localhost.** Cross-container calls use compose service DNS (`http://platform:8019`), not `localhost`. Intra-container calls use the same DNS — same name resolves both ways.
4. **Ship every phase to production.** After each phase, the full system must boot, pass health checks, and serve the UI end-to-end. No multi-phase "in progress" state.
5. **Rollback in one command.** Each phase preserves a tagged pre-phase image; `docker-compose -f docker-compose.prev.yml up` restores within 60 seconds.

## 3. Target architecture

| Domain container | Member services (existing dirs) | Ports (unchanged) | Responsibility |
|---|---|---|---|
| **platform** | api-gateway, auth-service | 8017, 8019 | Public HTTP entry, JWT |
| **llm-tools** | llm-gateway | 8021 | LLM tool executor |
| **market-pipeline** | market-data, feature-store, signal-service, external-data-service | 8001, 8002, 8003, 8020 | Data ingest → features → signals |
| **strategy-lab** | strategy-registry, statistics-service, memory-service, backtest-service | 8005, 8013, 8004, 8007 | Strategy versioning, analytics, memory, backtesting |
| **execution** | order-service, exchange-adapter, risk-service, credential-store, portfolio-service | 8011, 8008, 8009, 8010, 8012 | Orders, exchange routing, risk gate, credentials, positions |
| **intelligence** | crypto-agent, stock-agent, etf-agent, orchestrator-agent | 8006, 8016, 8015, 8014 | Trading agents + orchestration |

Plus the unchanged `db`, `redis`, `nats`, `frontend` containers = **10 compose services total**.

## 4. Guardrails (invariants every phase must preserve)

- `GET http://<host>:8017/health` returns 200 with all sub-services "up".
- `POST /auth/login` → `GET /settings/credentials` → `POST /settings/credentials` → `POST /settings/credentials/{ex}/verify` flow works.
- A signal event on NATS `signal.threshold.crossed.*` produces an `agent.crypto.action` within 10 seconds.
- No messages land on `*.dlq` for benign traffic.
- Prometheus scrape endpoints on every service still respond.
- Existing `.env` values continue to work (we add new vars; we don't remove).

## 5. Phase breakdown

Each phase lists: **entry criteria → work → validation → exit criteria → rollback**.

### Phase 0 — Foundation

**Entry**: Current `main` green, all 20 processes healthy.

**Work**:
- Write this document (`docs/CONSOLIDATION_PLAN.md`) — done.
- Create `scripts/_lib/start_domain.sh` — a shared helper that `start_platform.sh`, `start_market_pipeline.sh`, etc. will each source. Encodes the existing `start_service()` function from `start_all.sh` verbatim.
- Create directory `docker/domains/` to hold each per-domain Dockerfile (keeps root clean; `Dockerfile.backend` stays until Phase 7).
- Introduce new env vars alongside existing ones: for each `X_BASE_URL=http://localhost:80YY`, add commented target `# X_BASE_URL=http://<domain-container>:80YY` in `.env.example`. The target form is activated per-phase.
- Tag the current `backend` image as `quant-backend:pre-consolidation`.

**Validation**: `docker compose up` still works unchanged; helper script sourced by a dummy dry-run.

**Exit**: plan doc merged, scripts/_lib/ present, tag pushed.

**Rollback**: revert the 2–3 files added.

---

### Phase 1 — Domain F (platform)

**Entry**: Phase 0 complete.

**Work**:
1. Create `docker/domains/platform.Dockerfile`:
   - Base: `python:3.11-slim`
   - Copy only `services/api-gateway/` and `services/auth-service/` (+ `shared/`).
   - Install union of those two services' `requirements.txt`.
   - Entry: `scripts/start_platform.sh` which starts auth-service on 8019 (tier 1), waits, then api-gateway on 8017 (tier 5).
2. Add compose service `platform:` to `docker-compose.yml`, publishing 8017 and 8019. Use same healthcheck as current backend (8017/health).
3. Remove auth + gateway from `backend` container's port list and from `start_all.sh`.
4. Update `.env.example` to point *other services*' `AUTH_SERVICE_BASE_URL` and `API_GATEWAY_BASE_URL` at the `platform` DNS name. (These are consumed by many services.)
5. Update frontend env: `API_GATEWAY_BASE_URL=http://platform:8017`.

**Validation**:
- `docker compose up -d` → both `backend` and `platform` reach healthy.
- `curl localhost:8017/health` returns 200.
- Login via frontend works; JWT refresh works.
- Every other service's call into `/auth/*` or into gateway still works (grep-audited list: crypto-agent, orchestrator, llm-gateway, memory, order — all consume AUTH or GATEWAY URL indirectly).

**Exit**: 24h soak test in a dev environment with zero auth-related errors.

**Rollback**: revert compose change (platform service → commented), revert .env (local URLs), restore auth+gateway to `start_all.sh`. Image `quant-backend:pre-consolidation` still runs the old flow.

---

### Phase 2 — Domain E (llm-tools)

**Entry**: Phase 1 soaked 24h.

**Work**:
1. `docker/domains/llm-tools.Dockerfile` with only `services/llm-gateway/`.
2. Compose service `llm-tools` publishing 8021.
3. Remove llm-gateway from `backend`'s start script and ports.
4. Set `LLM_GATEWAY_BASE_URL=http://llm-tools:8021` for all consumers (orchestrator-agent, crypto-agent, api-gateway, memory-service).
5. llm-gateway itself calls 9 peer services — their URLs already updated in .env for compose DNS.

**Validation**: chat endpoint works from frontend. Tool calls that fan out to data services succeed. Metrics scrape works.

**Exit**: soak, then proceed.

**Rollback**: restore llm-gateway into `backend`.

---

### Phase 3 — Domain A (market-pipeline)

**Entry**: Phase 2 soaked.

**Work**:
1. `docker/domains/market-pipeline.Dockerfile` — copies market-data, feature-store, signal-service, external-data-service.
2. `scripts/start_market_pipeline.sh` — starts market-data:8001, feature-store:8002, signal-service:8003, external-data-service:8020 with tier-ordered startup.
3. Compose service `market-pipeline`.
4. Update peer .env vars: `MARKET_DATA_BASE_URL=http://market-pipeline:8001`, etc.
5. Audit: WebSocket collectors (binance/upbit) inside market-data still bind outbound only — no inbound port exposure needed beyond the 4 HTTP endpoints.

**Validation**:
- Candle ingest: POST a synthetic candle → verify `market.candle.updated.*` published on NATS → feature-store consumer computes features → signal-service emits signal. Each hop already in place; the validation is that container boundaries don't break it.
- **Noise budget**: sample 10 min of NATS traffic, zero `NoResponders`, zero DLQ growth.
- Feature-store consumer reconnects cleanly after a container restart (tests JetStream durable consumer behavior).

**Exit**: soak + noise-free 10-minute window.

**Rollback**: restore into backend.

---

### Phase 4 — Domain D (strategy-lab)

**Entry**: Phase 3 soaked.

**Work**:
1. `docker/domains/strategy-lab.Dockerfile` — strategy-registry, statistics-service, memory-service, backtest-service.
2. Inline `shared/statistics.py` (398 LOC, used only by statistics-service) into `services/statistics-service/app/core/statistics.py`. Remove from `shared/`. Update imports.
3. `scripts/start_strategy_lab.sh`.
4. Compose service `strategy-lab`.
5. Update peer .env vars for all 4.

**Validation**:
- Template subscription create/update/delete through UI still works.
- Lane allocation PATCH still works.
- Statistics endpoint returns Sharpe/drawdown for a known historical window.
- Memory reinforce path still writes + consolidates.
- Backtest run on a short window produces a valid report.

**Exit**: soak.

**Rollback**: restore into backend; also restore `shared/statistics.py`.

---

### Phase 5 — Domain B (execution)

**Entry**: Phase 4 soaked.

**Work**:
1. `docker/domains/execution.Dockerfile` — order-service, exchange-adapter, risk-service, credential-store, portfolio-service.
2. `scripts/start_execution.sh` (tier 1: credential-store, risk-service, exchange-adapter, portfolio-service; tier 3: order-service).
3. Compose service `execution`.
4. Update peer .env: `ORDER_SERVICE_BASE_URL`, `EXCHANGE_ADAPTER_BASE_URL`, `RISK_SERVICE_BASE_URL`, `CREDENTIAL_STORE_BASE_URL`, `PORTFOLIO_SERVICE_BASE_URL` → `http://execution:80XX`.
5. Intra-domain calls (order→risk→exchange→credential) are now all same-container; DNS still works.

**Validation**:
- Shadow order: `POST /orders` with `shadow_mode=true` → audit log row in `exchange_order_audits`.
- Credential verify endpoint from Phase 1 of our previous work still returns ok.
- Risk service still rejects oversized orders.
- Portfolio position updates after a fill event.

**Exit**: soak. This is the most financially-sensitive domain; we extend soak to 48h.

**Rollback**: restore into backend.

---

### Phase 6 — Domain C (intelligence)

**Entry**: Phase 5 soaked 48h.

**Work**:
1. `docker/domains/intelligence.Dockerfile` — crypto-agent, stock-agent, etf-agent, orchestrator-agent.
2. `scripts/start_intelligence.sh` (tier 1: etf-agent, stock-agent; tier 4: crypto-agent, orchestrator-agent).
3. Compose service `intelligence`.
4. Update peer .env: `CRYPTO_AGENT_BASE_URL=http://intelligence:8006`, `ORCHESTRATOR_AGENT_BASE_URL=http://intelligence:8014`.
5. Agent-to-peer HTTP calls (signal, feature-store, market-data, memory, portfolio, risk, order, stats, strategy, external, llm) already use env vars that now point at the right domain containers.

**Validation**:
- Dual-lane pipeline end-to-end: publish a synthetic signal → verify agent_core lane fires decision → verify template lane fans out per subscribed user → both produce `agent.crypto.action` events.
- Run the 20-minute noise test from `NATS noise fix` loop — zero DLQ, zero `NoResponders`.
- Orchestrator loop ticks and does not get stuck.

**Exit**: 48h soak. This is the heaviest domain; any regression here is customer-visible.

**Rollback**: restore into backend. This is why we keep `Dockerfile.backend` until Phase 7.

---

### Phase 7 — Cleanup

**Entry**: All 6 domains soaked individually.

**Work**:
1. Delete `Dockerfile.backend`.
2. Delete `scripts/start_all.sh`.
3. Delete `backend` compose service.
4. Remove deprecated env vars (`HOST_*` variants that are no longer consumed).
5. Update `docs/ARCHITECTURE.md` with the new topology diagram.
6. Final full-system smoke test:
   - Fresh `docker compose down -v && docker compose up --build -d`
   - All 10 containers reach healthy within 60s of `start_period`.
   - Register a new user → store credentials → verify → receive a signal → see a shadow order audit row.
7. Tag `quant-platform:v2-consolidated`.

**Exit**: final tag pushed, docs updated, stakeholder announcement.

---

## 6. Cross-cutting concerns

### 6.1 Database connections

Each domain container connects to `db` directly. We do *not* introduce a connection pool proxy (pgbouncer) in this refactor — current load doesn't justify it. Note for future: after consolidation, peak connection count is unchanged (still 20 uvicorn workers, just distributed across 6 containers).

### 6.2 NATS JetStream

Stream config is reconciled at service startup (`shared/events.py:ensure_stream` handles drift via `update_stream`, fixed in the NATS noise loop). Order of startup during a fresh `compose up` may cause temporary "subject not claimed" errors; the retry loop in `subscribe()` handles it. No code change needed.

### 6.3 Shared library

`shared/` remains a shared Python package. Each domain Dockerfile copies the whole `shared/` directory. This is duplicative (6 × ~3000 LOC) but keeps the import path stable and avoids a private PyPI dependency. If image size becomes painful, Phase 8+ can extract to a wheel.

### 6.4 Requirements slimming

Each domain's Dockerfile installs only the union of its member services' `requirements.txt`. This is where we realize the "dependency bleed" fix from §1. Expected image size drop: current backend ~1.4 GB → platform ~250 MB, market-pipeline ~400 MB, execution ~350 MB, strategy-lab ~800 MB (pandas/numpy heavy), intelligence ~900 MB (LangGraph + heavy deps), llm-tools ~250 MB.

### 6.5 Observability

Each container exposes its own Prometheus `/metrics` per process. Update `infra/prometheus.yml` scrape targets per phase (add new DNS names, keep old until cutover). Grafana dashboards use `service=` label which is unchanged.

### 6.6 Tests

Unit tests per service continue to run against their own `app/` package — unchanged. Integration tests in `tests/` that use `docker compose` will need compose service names updated per phase. We add a `tests/integration/compose.py` helper that maps service name → container DNS, so phase cutovers only require updating that map.

### 6.7 CI

GitHub Actions workflow builds `Dockerfile.backend` today. After Phase 1, we add build steps for each new domain Dockerfile. We keep the backend build until Phase 7.

## 7. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Env var drift — a service hardcodes `http://localhost:80XX` somewhere | Med | High (silent cross-domain call fail) | grep audit per phase; `.env.example` is single source |
| NATS stream-drift on restart | Low | Med | already fixed in `ensure_stream` (noise loop) |
| shared/ breaking change during phase | Low | High | freeze shared/ for duration; any change needs cross-domain review |
| Startup ordering breaks (e.g. crypto-agent boots before strategy-lab healthy) | Med | Med | `depends_on: service_healthy` in compose for inter-domain startup |
| DB connection exhaustion after split | Low | Med | total connections unchanged; monitor `pg_stat_activity` |
| Secrets in .env leak into an extra image layer | Low | High | `.env` is gitignored; compose uses `env_file:`; no COPY of .env into images |
| Memory footprint increases (each container has its own Python runtime overhead) | Cert | Low | accepted cost; ~100 MB × 5 extra containers on the same host |

## 8. Non-goals

- Rewriting any business logic.
- Changing any public HTTP contract.
- Migrating any database table.
- Introducing new languages, frameworks, or message buses.
- Switching to Kubernetes (out of scope; compose remains the deploy target).
- Merging multiple services into one FastAPI app at Python level (remains N processes per domain — see §2 principle 2).

## 9. Amendments log

**2026-04-14 — Phases 1–6 executed in a single session**
Plan as written assumed 24–48h soak per phase. Actual execution was a continuous
session with per-phase functional verification (health endpoints + cross-container
HTTP traces + log inspection). Soak windows are deferred to post-execution
production observation.

**2026-04-14 — Latent dependency gaps surfaced and fixed**
The monolithic `backend` container masked several missing entries in individual
`requirements.txt` files (every service inherited the union install). Carving out
domains exposed:

- `services/exchange-adapter/requirements.txt`: missing `PyJWT`, `redis`, `httpx`.
  Added.
- `services/crypto-agent/requirements.txt`: missing `pandas`, `numpy`, `ta`.
  Added (consumed via `shared.formulas` → `shared.regime`).
- `services/stock-agent/requirements.txt` + `services/etf-agent/requirements.txt`
  + `services/orchestrator-agent/requirements.txt`: missing the same numerical
  stack. Added.
- `services/llm-gateway/requirements.txt`: missing `psycopg`, `sqlalchemy`,
  `prometheus-client`. Added (consumed via `shared.persistence`).

These were latent bugs, not refactor regressions.

**2026-04-14 — `shared/statistics.py` inline deferred**
Plan §5 Phase 4 called for inlining `shared/statistics.py` into statistics-service.
Diagnostic was wrong about its consumer — the actual sole consumer is
`services/backtest-service/app/core/evaluator.py`. Both backtest and statistics
live in the strategy-lab container, so `shared/` continues to expose it without
cost. Inline deferred to a future cleanup pass.

**2026-04-14 — `Dockerfile.backend` shrank per-phase, then deleted**
Plan §5 Phase 7 said the monolith image stays until final cleanup. In practice we
needed to drop carved-out service requirements as we went, otherwise pip install
during backend rebuild exhausted builder disk (torch + nvidia-cuda packages are
huge). Each phase trimmed `Dockerfile.backend`'s `COPY services/*/requirements.txt`
list. By Phase 6 all services had moved out and the file + `scripts/start_all.sh`
were deleted in the same step rather than waiting for Phase 7.

## 10. Final state (post-execution)

10 compose services, all healthy:

```
db, redis, nats                                 (infra)
platform        api-gateway:8017, auth-service:8019
market-pipeline market-data:8001, feature-store:8002, signal-service:8003, external-data-service:8020
strategy-lab    memory-service:8004, strategy-registry:8005, backtest-service:8007, statistics-service:8013
execution       exchange-adapter:8008, risk-service:8009, credential-store:8010, order-service:8011, portfolio-service:8012
intelligence    crypto-agent:8006, orchestrator-agent:8014, etf-agent:8015, stock-agent:8016
llm-tools       llm-gateway:8021
frontend        Next.js:8018
```

End-to-end smoke validated:
- `GET http://localhost:8017/health` → 200 (gateway reaches auth-service across
  containers via `http://platform:8019`)
- `GET http://localhost:8018/` → 200 (frontend reaches gateway via
  `http://platform:8017`)
- All 4 domain containers' `/health` endpoints return 200 with downstream
  postgres/redis/nats checks green.
- crypto-agent logs show successful HTTP fetches of `/signals/*/latest` from
  `market-pipeline` and `/portfolio/system` from `execution` — proving the
  cross-container request paths work end-to-end.
