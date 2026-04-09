#!/bin/bash
set -e

# ============================================================
# Unified backend startup script
# Each service runs as a separate uvicorn process with its own
# PYTHONPATH so app/ packages don't conflict.
# ============================================================

PIDS=()

log() { echo "[$(date -u +%H:%M:%S)] $1"; }

start_service() {
    local name=$1
    local dir=$2
    local port=$3
    log "Starting $name on :$port"
    (
        cd /code/services/$dir
        PYTHONPATH=/code:/code/services/$dir exec uvicorn app.main:app \
            --host 0.0.0.0 --port $port \
            --log-level warning \
            --no-access-log
    ) &
    PIDS+=($!)
}

# --- Tier 1: Services with no internal dependencies ---
start_service "memory-service"        memory-service        8004
start_service "strategy-registry"     strategy-registry     8005
start_service "llm-gateway"           llm-gateway           8021
start_service "external-data-service" external-data-service 8020
start_service "backtest-service"      backtest-service      8007
start_service "etf-agent"             etf-agent             8015
start_service "stock-agent"           stock-agent           8016
start_service "credential-store"      credential-store      8010
start_service "risk-service"          risk-service          8009
start_service "exchange-adapter"      exchange-adapter      8008

# --- Tier 2: Data pipeline ---
start_service "market-data"           market-data           8001
start_service "feature-store"         feature-store         8002
start_service "signal-service"        signal-service        8003

# --- Tier 3: Execution (depends on risk, exchange, credential) ---
start_service "portfolio-service"     portfolio-service     8012
start_service "statistics-service"    statistics-service     8013
start_service "order-service"         order-service         8011

# --- Tier 4: Agent (depends on signal, memory, strategy, llm) ---
start_service "crypto-agent"          crypto-agent          8006
start_service "orchestrator-agent"    orchestrator-agent    8014

# --- Tier 5: Gateway (depends on auth + all services) ---
start_service "auth-service"          auth-service          8019

# Wait a moment for auth-service to be ready before gateway
sleep 3

start_service "api-gateway"           api-gateway           8017

log "All ${#PIDS[@]} services started."

# Monitor: if ANY process dies, log it but keep running
while true; do
    for i in "${!PIDS[@]}"; do
        if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
            wait "${PIDS[$i]}" 2>/dev/null || true
            log "WARNING: Process ${PIDS[$i]} exited"
            unset 'PIDS[$i]'
        fi
    done
    # If all processes died, exit
    if [ ${#PIDS[@]} -eq 0 ]; then
        log "All processes exited. Shutting down."
        exit 1
    fi
    sleep 5
done
