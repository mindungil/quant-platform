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
