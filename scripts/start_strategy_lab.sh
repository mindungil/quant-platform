#!/bin/bash
# ============================================================
# start_strategy_lab.sh — strategy-lab domain entrypoint
#
# Processes:
#   memory-service      :8004  (Tier 1 — embeddings + decision memory)
#   strategy-registry   :8005  (Tier 1 — model/subscription registry)
#   backtest-service    :8007  (Tier 1 — nautilus/backtrader)
#   statistics-service  :8013  (Tier 3 — trade metrics, depends on portfolio HTTP;
#                                consumer retries handle peer readiness)
# ============================================================

set -e

# shellcheck source=_lib/start_domain.sh
source /code/scripts/_lib/start_domain.sh

start_service "memory-service"     "memory-service"     8004
start_service "strategy-registry"  "strategy-registry"  8005
start_service "backtest-service"   "backtest-service"   8007
start_service "statistics-service" "statistics-service" 8013

wait_for_pids
