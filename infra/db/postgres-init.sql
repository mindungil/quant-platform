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
