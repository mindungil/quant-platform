#!/bin/bash
# ============================================================
# start_market_pipeline.sh — market-pipeline domain entrypoint
#
# Processes (tiered to match the old start_all.sh ordering):
#   Tier 1 (no internal deps):
#     external-data-service :8020
#   Tier 2 (data pipeline):
#     market-data           :8001  (Binance/Upbit WebSocket collectors)
#     feature-store         :8002  (indicator computation + NATS consumer)
#     signal-service        :8003  (factor scoring + threshold publish)
#
# All four talk via NATS JetStream (candle → feature → signal), so
# startup order is best-effort — JetStream consumers retry until
# their stream is reachable.
# ============================================================

set -e

# shellcheck source=_lib/start_domain.sh
source /code/scripts/_lib/start_domain.sh

# Tier 1
start_service "external-data-service" "external-data-service" 8020

# Tier 2
start_service "market-data"   "market-data"   8001
start_service "feature-store" "feature-store" 8002
start_service "signal-service" "signal-service" 8003

wait_for_pids
