-- Create the market database (used by market-data, feature-store, signal-service)
SELECT 'CREATE DATABASE market'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'market')\gexec
