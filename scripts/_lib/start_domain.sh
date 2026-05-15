#!/bin/bash
# ============================================================
# start_domain.sh — shared helper for per-domain start scripts
#
# Sourced (not executed) by each domain's start script, e.g.
#   scripts/start_platform.sh, scripts/start_market_pipeline.sh
#
# Provides:
#   - PIDS[] array
#   - start_service <name> <dir> <port>
#   - wait_for_pids (blocking monitor loop, restarts-on-exit off by design)
#
# Rationale: each service dir has its own app/ package, so we set a
# per-service PYTHONPATH to avoid import collisions when multiple
# uvicorn processes run inside the same container. This matches the
# original scripts/start_all.sh behavior verbatim; the only difference
# is that domain scripts start only their own subset of services.
# ============================================================

set -e

# Exported so domain scripts can append their own PIDs
PIDS=()

log() { echo "[$(date -u +%H:%M:%S)] $1"; }

start_service() {
    local name=$1
    local dir=$2
    local port=$3
    log "Starting $name on :$port"
    (
        cd /code/services/"$dir"
        PYTHONPATH=/code:/code/services/"$dir" exec uvicorn app.main:app \
            --host 0.0.0.0 --port "$port" \
            --log-level warning \
            --no-access-log
    ) &
    PIDS+=($!)
}

# Block until ANY child process exits, then exit the container so compose
# restart policy can take over. Previously we only exited when ALL children
# died, which let a single dead service (e.g. memory-service) silently rot
# inside an otherwise-"healthy" container — discovered after memory-service
# was dead for ~2 weeks while three siblings kept the container alive.
wait_for_pids() {
    log "Domain started with ${#PIDS[@]} processes. Entering monitor loop."
    while true; do
        for i in "${!PIDS[@]}"; do
            if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
                wait "${PIDS[$i]}" 2>/dev/null || true
                log "FATAL: Process ${PIDS[$i]} exited — restarting container."
                exit 1
            fi
        done
        sleep 5
    done
}
