#!/bin/bash
# ============================================================
# start_intelligence.sh — intelligence domain entrypoint
#
# Processes (tier-ordered to match the old start_all.sh):
#   Tier 1 (leaf agents):
#     etf-agent           :8015
#     stock-agent         :8016
#   Tier 4 (depends on signals / memory / strategy / llm):
#     crypto-agent        :8006
#     orchestrator-agent  :8014
#
# The orchestrator fans out to the three asset agents via HTTP;
# after consolidation they share a compose DNS name so the call
# stays local to this container.
# ============================================================

set -e

# shellcheck source=_lib/start_domain.sh
source /code/scripts/_lib/start_domain.sh

# Tier 1
start_service "etf-agent"          "etf-agent"          8015
start_service "stock-agent"        "stock-agent"        8016

# Tier 4
start_service "crypto-agent"       "crypto-agent"       8006
start_service "orchestrator-agent" "orchestrator-agent" 8014

wait_for_pids
