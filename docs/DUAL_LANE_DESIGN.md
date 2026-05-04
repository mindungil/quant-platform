# Dual-Lane Trading Architecture

**Status:** Draft (pending user approval)
**Owner:** Platform
**Last updated:** 2026-04-14

## 1. Motivation

The current strategy-registry conflates two different products into a single
`ACTIVE` slot per `(user_id, asset_type)`:

1. **Validated alpha engine** — the platform's pruned v4 engine (Sharpe
   1.35–1.54 on 8y + funding). This is the house's product.
2. **User-selected templates** — presets a user picks from `/templates`. This
   is a user-driven product.

Today `POST /strategies/templates/{id}/activate` writes a `DRAFT` record that
the agent never reads (the agent only reads `ACTIVE`). Result: the button
does nothing observable. Any fix that promotes templates to `ACTIVE`
contaminates the engine's lane — user choices and the validated engine share
one record, one PnL curve, one Sharpe.

This document splits them into two independent **lanes** that share a single
execution account but keep separate books.

## 2. Design goals

- **Separation of concerns.** Engine performance evaluated independently of
  user template choices.
- **User control.** Capital split between lanes is user-configurable.
- **No silent failures.** Subscribing to a template produces a visible,
  traceable effect on trading.
- **Global safety.** One set of account-level risk limits applies to the sum
  of both lanes.
- **Respects alpha testing protocol.** Templates never enter the validated
  ensemble — they live in a separate lane.

## 3. Lane definitions

| Lane | Source | Status machine | Who decides what trades |
|------|--------|---------------|-------------------------|
| `agent_core` | strategy-registry `ACTIVE` strategy for user (falls back to bootstrap) | existing `DRAFT → PENDING → TESTED → SHADOW → ACTIVE` | Platform / engine |
| `user_template` | user subscriptions in `user_template_subscriptions` | `enabled / paused / stopped` only (no SHADOW) | User |

Both lanes run through the **same crypto-agent pipeline**, one iteration per
lane per tick. The only difference is where `state.strategy` and the `lane`
tag come from.

## 4. Data model

### 4.1 New table: `user_template_subscriptions`

```sql
CREATE TABLE user_template_subscriptions (
    id              TEXT PRIMARY KEY,             -- uuid
    user_id         TEXT NOT NULL,
    template_id     TEXT NOT NULL,                -- references shared.strategy_templates id
    asset_type      TEXT NOT NULL,                -- 'crypto' for now
    status          TEXT NOT NULL DEFAULT 'enabled',  -- enabled | paused | stopped
    weight          DOUBLE PRECISION NOT NULL DEFAULT 1.0,  -- relative weight if user subscribes to multiple templates
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, template_id, asset_type)
);
CREATE INDEX ix_uts_user_active ON user_template_subscriptions(user_id, asset_type)
  WHERE status = 'enabled';
```

A user can subscribe to multiple templates; their `weight` field lets the
user tilt between templates within the Template lane. The lane itself
composes them into a single strategy object (weighted sum of each template's
factor weights) before the agent runs.

### 4.2 New table: `lane_allocations`

```sql
CREATE TABLE lane_allocations (
    user_id          TEXT NOT NULL,
    asset_type       TEXT NOT NULL,
    agent_pct        DOUBLE PRECISION NOT NULL DEFAULT 0.70 CHECK (agent_pct BETWEEN 0 AND 1),
    template_pct     DOUBLE PRECISION NOT NULL DEFAULT 0.30 CHECK (template_pct BETWEEN 0 AND 1),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, asset_type),
    CHECK (agent_pct + template_pct <= 1.0)  -- remainder is cash
);
```

Default: 70/30. Sum ≤ 1.0 so user can deliberately hold cash.

### 4.3 Column additions

- `strategy_records.lane TEXT NOT NULL DEFAULT 'agent_core'`
  Values: `agent_core | user_template`.
- `orders.lane TEXT NOT NULL DEFAULT 'agent_core'`
  Set by agent at order placement.
- `positions.lane TEXT NOT NULL DEFAULT 'agent_core'`
  Position bookkeeping per lane (virtual separation — one exchange account,
  two logical books).

Migrations are additive only; existing rows backfill to `agent_core`.

## 5. API surface

### 5.1 Template subscriptions (strategy-registry)

```
GET    /templates/subscriptions                  → list current user's subscriptions
POST   /templates/subscriptions                  → { template_id, asset_type, weight? }
PATCH  /templates/subscriptions/{id}             → { status?, weight? }
DELETE /templates/subscriptions/{id}             → mark stopped and remove
```

Replaces `POST /strategies/templates/{id}/activate` (kept as alias for one
release, returns subscription instead of DRAFT strategy).

### 5.2 Lane allocation (risk-service or settings)

```
GET    /settings/lane-allocation?asset_type=crypto       → { agent_pct, template_pct }
PATCH  /settings/lane-allocation                         → { asset_type, agent_pct, template_pct }
```

Validation: `agent_pct + template_pct ≤ 1.0`, each in `[0, 1]`.

### 5.3 Performance (statistics-service)

```
GET /performance?lane=agent|user_template|all     → PnL, Sharpe, trade count by lane
```

### 5.4 Alerts (new, event-driven — reuses NATS)

Two new events published by the agent pipeline, consumed by the frontend via
existing WebSocket/SSE channel:

- `lane.signal_collision` — both lanes produced same-direction signal on same
  asset in same tick.
- `lane.opposite_collision` — lanes produced opposite signals.
- `lane.risk_rejection` — a lane's order was rejected by the global risk
  gate.

Alerts are **notifications only**; they do not change routing.

## 6. Agent pipeline changes

File: `services/crypto-agent/app/services/pipeline.py`

### 6.1 Current flow (single lane)

```
fetch_signal → retrieve_memory → select_strategy → apply_strategy
  → check_risk → decide → publish_order
```

`select_strategy` calls `strategy_client.get_active_strategy("crypto")`.

### 6.2 New flow (dual lane)

```
                                 ┌── build_agent_core_strategy ─┐
fetch_signal → retrieve_memory ──┤                              ├── foreach lane:
                                 └── build_user_template_strategy ┘      apply_strategy
                                                                         → check_risk (GLOBAL gate)
                                                                         → decide
                                                                         → tag order with lane
                                                                         → publish_order
                                                                         → emit collision alerts
```

Key rules:

- **Lane budget.** Before `apply_strategy`, pipeline computes each lane's
  notional budget: `equity × lane_pct`. Position sizing inside the lane is
  capped by its budget.
- **Independent state.** Each lane carries its own `AgentState` copy;
  `signal` and `memories` can be shared (cheap to fetch once), but
  `strategy`, `action`, `adjusted_score`, `order_id` are per-lane.
- **Global risk check.** `check_risk` aggregates both lanes' proposed deltas
  against the account-level limits (not lane-level). See §7.
- **Order tagging.** `order_client.place_order(..., lane=lane)` — order-service
  persists `orders.lane` and passes it through.
- **Collision emission.** After both lanes decide, a final merge step
  compares their `action`/direction. Same symbol + same direction → signal
  collision alert. Opposite → opposite collision alert. Both are logged and
  published; neither nets the orders.

### 6.3 Template lane strategy composition

`build_user_template_strategy` pseudocode:

```python
subs = strategy_client.list_subscriptions(user_id, asset_type, status="enabled")
if not subs:
    state.strategy = None   # lane idle, no orders
    return state
weights = defaultdict(float)
total_w = sum(s.weight for s in subs) or 1.0
for sub in subs:
    tpl = shared.strategy_templates.get_template(sub.template_id)
    for factor, w in tpl["weights"].items():
        weights[factor] += w * (sub.weight / total_w)
state.strategy = Strategy(
    id=f"template-lane:{user_id}",
    lane="user_template",
    name="User template composite",
    weights=dict(weights),
    thresholds={"entry": 0.6, "exit": -0.35},
    ...
)
```

Multiple subscribed templates are merged into one synthetic strategy per
tick. No DB write required for the synthetic strategy.

## 7. Global risk gate + conflict policy

### 7.1 Aggregate pre-trade check

`risk-service /risk/portfolio-check` is extended to accept a list of
proposed orders with lane tags:

```
POST /risk/portfolio-check
{
  "user_id": "...",
  "proposals": [
    {"lane": "agent_core",    "asset": "BTCUSDT", "side": "BUY", "notional": 1000},
    {"lane": "user_template", "asset": "BTCUSDT", "side": "BUY", "notional":  400}
  ]
}
→
{
  "approvals": [
    {"lane": "agent_core",    "approved": true},
    {"lane": "user_template", "approved": false, "reason": "daily_loss_limit"}
  ],
  "global_state": { "daily_pnl": ..., "current_exposure": ..., "leverage": ... }
}
```

Limits checked against `sum(lane_proposals)`:

- Daily realized+unrealized loss floor.
- Max total notional exposure.
- Max leverage.

### 7.2 Tiebreak policy (platform decision)

When only a subset of the proposals fits under the global cap:

1. **Agent lane first.** Validated alphas serve before user picks.
2. **Within a lane,** preserve the original submission order (no intra-lane
   reshuffle).
3. **Rejected proposals emit `lane.risk_rejection`** — user sees exactly
   which order was skipped and why.

This is overridable in a future `user.lane_priority` setting but ships with
`agent_core` priority as default.

### 7.3 Opposite-signal handling

- **No netting.** Agent buys and template sells are both sent. Account
  position nets to zero but each lane books its own fill. Cost: fees on both
  sides. Benefit: each lane's PnL stays attributable.
- **Alert.** `lane.opposite_collision` fires so user can manually pause a
  lane if they want.

### 7.4 Duplicate-signal handling

- **Both orders sent** at their respective lane-budget sizes.
- **Alert** `lane.signal_collision` fires for user awareness.

## 8. Frontend changes

### 8.1 `/templates` page

- On mount, fetch `GET /templates/subscriptions` alongside
  `GET /strategies/templates`.
- Each template card shows state badge:
  - `구독 중` (green dot) — user has `enabled` sub.
  - `일시중지` (amber) — `paused`.
  - none — not subscribed.
- Primary button text:
  - Not subscribed → `구독`.
  - Subscribed + enabled → `일시중지`.
  - Paused → `재개`.
  - Secondary link → `구독 해지`.
- Toast replaced by persistent card state update.

### 8.2 `/settings` page

- New section "레인 자본 배분" with two sliders (Agent % / Template %),
  constrained to sum ≤ 100. Remainder shown as "현금 비중".
- `PATCH /settings/lane-allocation` on save.

### 8.3 `/dashboard`

- Split into two equal cards, side by side:
  - **에이전트 레인** — current strategy name, PnL (D/W/M), open positions,
    allocation %.
  - **템플릿 레인** — subscribed template list, PnL, open positions,
    allocation %, "0개 구독 중" empty state linking to `/templates`.
- Below: combined global risk widget (daily loss, exposure, leverage)
  showing consumption vs limits.
- Alert strip for recent `lane.*` events (last 5).

### 8.4 `/performance`

- Lane filter tabs: `전체 | 에이전트 | 템플릿`.
- Per-tab Sharpe, drawdown, trade count.

## 9. Migration plan

1. **Phase 1 — additive schema** (no behavior change).
   - Add `lane` columns with defaults.
   - Create `user_template_subscriptions` + `lane_allocations` tables.
   - Deploy strategy-registry + order-service with dual-write (still only
     one lane observed).
2. **Phase 2 — dual-lane agent** (feature-flagged per user).
   - Ship pipeline changes behind `DUAL_LANE_ENABLED` env flag.
   - Manual QA with one internal account.
3. **Phase 3 — enable globally.**
   - Default `lane_allocations` row inserted for existing users
     (70/30 agent/template).
   - `/templates/.../activate` alias returns subscription.
4. **Phase 4 — frontend rollout.**
   - Ship `/templates`, `/settings`, `/dashboard` changes together.
5. **Phase 5 — deprecate old path.**
   - Remove `/strategies/templates/{id}/activate` alias (one release later).

Each phase is independently deployable and reversible (flip env flag or
revert commit).

## 10. Testing

- **Unit.**
  - Subscription CRUD, weight composition math.
  - `lane_allocations` sum validation.
  - Global risk aggregation across lanes.
- **Integration.**
  - Subscribe template → next tick produces template-lane order with
    `lane='user_template'` persisted.
  - Two lanes proposing > global cap → agent_core approved, user_template
    rejected with event emitted.
  - Opposite-signal scenario → both orders booked, alert emitted, net
    exchange position matches expectation.
- **E2E (manual).**
  - Login → subscribe template on `/templates` → see badge update →
    dashboard shows template lane active → settings allocation change
    reflected in next tick's notional.

## 11. Open questions

1. **Template lane sizing when multiple subscriptions.** Do we split the
   template budget per subscription (weighted) or apply the full budget to
   the composite strategy? Current doc: composite uses full template budget
   (simpler, matches "one strategy per lane" invariant). Flag if this is
   wrong.
2. **Paper-trade toggle per lane.** Not in v1 but likely desired. Reserved
   column `lane_allocations.paper_only BOOLEAN DEFAULT FALSE` to make future
   addition cheap.
3. **Dashboard refresh cadence.** Existing WS channel sufficient? Need to
   confirm with frontend lead (likely: yes).

## 12. Non-goals

- True sub-account separation at exchange level (deferred; virtual
  separation suffices for v1).
- Cross-lane ensemble weighting (explicitly rejected by alpha testing
  protocol).
- User-editable custom strategies in template lane (templates only; custom
  strategy authoring is a separate product).
