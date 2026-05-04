#!/bin/bash
# ============================================================
# start_platform.sh — platform domain entrypoint
#
# Processes:
#   auth-service    :8019  (tier 1 — no internal deps)
#   api-gateway     :8017  (tier 5 — depends on auth)
#
# Auth is brought up first; gateway waits briefly so its
# startup health probe against auth succeeds on the first try.
# ============================================================

set -e

# shellcheck source=_lib/start_domain.sh
source /code/scripts/_lib/start_domain.sh

# Tier 1
start_service "auth-service" "auth-service" 8019

# Give auth a few seconds to bind + complete DB schema bootstrap
sleep 3

# Tier 5
start_service "api-gateway"  "api-gateway"  8017

wait_for_pids
