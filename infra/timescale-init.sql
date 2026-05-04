CREATE TABLE IF NOT EXISTS market_candles (
    asset TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    anomaly_detected BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (asset, timestamp)
);

CREATE TABLE IF NOT EXISTS feature_history (
    asset TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (asset, timestamp)
);

CREATE TABLE IF NOT EXISTS signal_history (
    asset TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    PRIMARY KEY (asset, timestamp)
);

-- ═══════════════════════════════════════════════════════════════
-- Sentiment Data Center tables
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sentiment_items (
    id TEXT NOT NULL,
    asset TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    title TEXT NOT NULL,
    body TEXT,
    nlp_score DOUBLE PRECISION,
    nlp_model TEXT,
    nlp_confidence DOUBLE PRECISION,
    community_score DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (asset, timestamp, id)
);

CREATE TABLE IF NOT EXISTS sentiment_hourly (
    asset TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    nlp_mean DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    nlp_median DOUBLE PRECISION,
    nlp_std DOUBLE PRECISION,
    nlp_count INTEGER NOT NULL DEFAULT 0,
    news_score DOUBLE PRECISION,
    social_score DOUBLE PRECISION,
    community_score DOUBLE PRECISION,
    total_items INTEGER NOT NULL DEFAULT 0,
    bullish_count INTEGER NOT NULL DEFAULT 0,
    bearish_count INTEGER NOT NULL DEFAULT 0,
    neutral_count INTEGER NOT NULL DEFAULT 0,
    fng_value INTEGER,
    lunarcrush_galaxy DOUBLE PRECISION,
    composite_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (asset, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_sentiment_items_source_ts
    ON sentiment_items (source, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_hourly_asset_ts
    ON sentiment_hourly (asset, timestamp DESC);

-- ═══════════════════════════════════════════════════════════════
-- RAG Event Store: 뉴스 임베딩 + 시세 결과
-- ═══════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS event_embeddings (
    id TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    body_preview TEXT,
    chunk_text TEXT NOT NULL,
    embedding vector(1024),
    nlp_score DOUBLE PRECISION,
    nlp_confidence DOUBLE PRECISION,
    tier TEXT NOT NULL DEFAULT '1',
    price_at_event DOUBLE PRECISION,
    volume_zscore DOUBLE PRECISION,
    fng_value INTEGER,
    volatility DOUBLE PRECISION,
    return_1h DOUBLE PRECISION,
    return_6h DOUBLE PRECISION,
    return_24h DOUBLE PRECISION,
    max_drawdown_24h DOUBLE PRECISION,
    labeled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_event_emb_asset_ts
    ON event_embeddings (asset, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_event_emb_unlabeled
    ON event_embeddings (labeled_at) WHERE labeled_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_event_emb_tier2
    ON event_embeddings (tier, asset) WHERE tier = '2';

-- ═══════════════════════════════════════════════════════════════
-- Data retention: keep raw items 1 year, hourly aggregates forever
-- Run periodically via cron or manual maintenance
-- ═══════════════════════════════════════════════════════════════

-- Cleanup function for old raw sentiment items (body text is heavy)
CREATE OR REPLACE FUNCTION cleanup_old_sentiment_items() RETURNS INTEGER AS $$
DECLARE
    deleted INTEGER;
BEGIN
    DELETE FROM sentiment_items
    WHERE timestamp < NOW() - INTERVAL '365 days';
    GET DIAGNOSTICS deleted = ROW_COUNT;
    RETURN deleted;
END;
$$ LANGUAGE plpgsql;
