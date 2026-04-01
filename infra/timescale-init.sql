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
