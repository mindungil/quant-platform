#!/bin/bash
# ============================================================
# start_execution.sh — execution domain entrypoint
#
# Processes (tier-ordered):
#   Tier 1 (no internal deps):
#     credential-store   :8010
#     risk-service       :8009
#     exchange-adapter   :8008
#   Tier 3 (depends on tier 1 intra-domain):
#     portfolio-service  :8012
#     order-service      :8011
# ============================================================

set -e

# shellcheck source=_lib/start_domain.sh
source /code/scripts/_lib/start_domain.sh

# Tier 1
start_service "credential-store"   "credential-store"   8010
start_service "risk-service"       "risk-service"       8009
start_service "exchange-adapter"   "exchange-adapter"   8008

# Tier 3
start_service "portfolio-service"  "portfolio-service"  8012
start_service "order-service"      "order-service"      8011

wait_for_pids
