-- Create the market database for TimescaleDB (candles, features, signals)
SELECT 'CREATE DATABASE market'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'market')\gexec

-- Enable extensions on the default platform database
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Enable extensions on the market database
\c market
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
