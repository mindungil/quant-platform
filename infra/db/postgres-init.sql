CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    memory_type TEXT NOT NULL,
    asset TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    signal_score DOUBLE PRECISION NOT NULL,
    action TEXT NOT NULL,
    strategy_id TEXT,
    reasoning TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
    links JSONB NOT NULL DEFAULT '[]'::jsonb,
    link_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_reinforced_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    name TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    indicators JSONB NOT NULL,
    weights JSONB NOT NULL,
    thresholds JSONB NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    backtest_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    shadow_metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Agent chat conversations
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls JSONB,
    tool_name TEXT,
    tool_call_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conv ON chat_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

-- ---------------------------------------------------------------------------
-- Row-Level Security (RLS)
-- Users can only access rows where user_id matches the session variable.
-- Admin role bypasses RLS.
-- ---------------------------------------------------------------------------

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'admin') THEN CREATE ROLE admin; END IF; END $$;

-- memory_records
ALTER TABLE memory_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_user_isolation ON memory_records;
CREATE POLICY rls_user_isolation ON memory_records
    USING (user_id = current_setting('app.current_user_id', true)::text)
    WITH CHECK (user_id = current_setting('app.current_user_id', true)::text);
DROP POLICY IF EXISTS rls_admin_bypass ON memory_records;
CREATE POLICY rls_admin_bypass ON memory_records TO admin USING (true) WITH CHECK (true);

-- strategies
ALTER TABLE strategies ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_user_isolation ON strategies;
CREATE POLICY rls_user_isolation ON strategies
    USING (user_id = current_setting('app.current_user_id', true)::text)
    WITH CHECK (user_id = current_setting('app.current_user_id', true)::text);
DROP POLICY IF EXISTS rls_admin_bypass ON strategies;
CREATE POLICY rls_admin_bypass ON strategies TO admin USING (true) WITH CHECK (true);

-- conversations
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_user_isolation ON conversations;
CREATE POLICY rls_user_isolation ON conversations
    USING (user_id = current_setting('app.current_user_id', true)::text)
    WITH CHECK (user_id = current_setting('app.current_user_id', true)::text);
DROP POLICY IF EXISTS rls_admin_bypass ON conversations;
CREATE POLICY rls_admin_bypass ON conversations TO admin USING (true) WITH CHECK (true);


-- ──────────────────────────────────────────────────────────────────
-- Phase C: alpha incubator
--
-- Holds candidate alphas that must pass standalone 8-year validation
-- before being considered for the production ensemble. Enforces the
-- "never add untested alphas" rule: promotion requires Sharpe ≥ 1.0,
-- max drawdown ≤ 30%, and IC information ratio ≥ 0.5 across the full
-- 8-year window AND a final 20% out-of-sample holdout.
--
-- Status machine:
--   PENDING     — submitted, not yet evaluated
--   EVALUATING  — run in progress (idempotent lease)
--   EVALUATED   — stats written, no promotion decision yet
--   PROMOTED    — passed gates, auto-listed in alpha ensemble
--   REJECTED    — failed one or more gates (reason in gate_report)
--   DEPRECATED  — previously promoted but live perf decayed
-- ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alpha_incubator_candidates (
    id TEXT PRIMARY KEY,
    alpha_name TEXT NOT NULL,              -- entry in shared.alpha.registry
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING',
    submitted_by TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    evaluated_at TIMESTAMPTZ,
    promoted_at TIMESTAMPTZ,
    -- Evaluation stats: filled when status moves past EVALUATING.
    sharpe_full DOUBLE PRECISION,          -- full-period Sharpe
    sharpe_oos DOUBLE PRECISION,           -- last 20% holdout Sharpe
    max_drawdown DOUBLE PRECISION,         -- (positive fraction, e.g. 0.28)
    ic DOUBLE PRECISION,                   -- Spearman IC with forward return
    ic_ir DOUBLE PRECISION,                -- IC information ratio
    turnover DOUBLE PRECISION,             -- avg |Δposition| per bar
    n_bars INTEGER,
    asset TEXT,
    gate_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT incubator_status_valid CHECK (
        status IN ('PENDING', 'EVALUATING', 'EVALUATED', 'PROMOTED', 'REJECTED', 'DEPRECATED')
    )
);

CREATE INDEX IF NOT EXISTS alpha_incubator_status_idx ON alpha_incubator_candidates (status);
CREATE INDEX IF NOT EXISTS alpha_incubator_submitted_at_idx ON alpha_incubator_candidates (submitted_at DESC);
