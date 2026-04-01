#!/bin/bash
set -e

# ============================================================
# Unified backend startup script
# Each service runs as a separate uvicorn process.
# They share the container network (localhost) but have
# isolated Python paths so app/ packages don't conflict.
# ============================================================

log() { echo "[$(date -u +%H:%M:%S)] $1"; }

start_service() {
    local name=$1
    local dir=$2
    local port=$3
    log "Starting $name on :$port"
    cd /code/services/$dir
    PYTHONPATH=/code:/code/services/$dir uvicorn app.main:app \
        --host 0.0.0.0 --port $port \
        --log-level info \
        --no-access-log &
    cd /code
}

# --- Data pipeline ---
start_service "market-data"           market-data           8001
start_service "feature-store"         feature-store         8002
start_service "signal-service"        signal-service        8003
start_service "external-data-service" external-data-service 8020

# --- Trading engine ---
start_service "memory-service"        memory-service        8004
start_service "strategy-registry"     strategy-registry     8005
start_service "crypto-agent"          crypto-agent          8006
start_service "llm-gateway"           llm-gateway           8021
start_service "etf-agent"             etf-agent             8015
start_service "stock-agent"           stock-agent           8016
start_service "orchestrator-agent"    orchestrator-agent    8014

# --- Execution ---
start_service "backtest-service"      backtest-service      8007
start_service "exchange-adapter"      exchange-adapter      8008
start_service "risk-service"          risk-service          8009
start_service "credential-store"      credential-store      8010
start_service "order-service"         order-service         8011
start_service "portfolio-service"     portfolio-service     8012
start_service "statistics-service"    statistics-service     8013

# --- Gateway ---
start_service "auth-service"          auth-service          8019
start_service "api-gateway"           api-gateway           8017

log "All services started. Waiting..."

# Wait for any process to exit, then exit the container
wait -n
exit $?
