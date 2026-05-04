#!/bin/bash
# ============================================================
# start_llm_tools.sh — llm-tools domain entrypoint
#
# Processes:
#   llm-gateway  :8021  (stateless LLM tool executor)
# ============================================================

set -e

# shellcheck source=_lib/start_domain.sh
source /code/scripts/_lib/start_domain.sh

start_service "llm-gateway" "llm-gateway" 8021

wait_for_pids
