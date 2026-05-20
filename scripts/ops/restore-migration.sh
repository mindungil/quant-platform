#!/usr/bin/env bash
# Restore a migration package created by package-migration.sh.
#
#   ./scripts/ops/restore-migration.sh <package.tar.gz> [target-dir]
#
# Default target-dir is ./quant-restored. Source is the tarball produced
# on the original host. The target host must already have docker +
# docker compose v2 installed.
#
# What this does NOT do for you:
#   - It does NOT fill in .env. You must edit it after step 4 — secrets
#     should never live in the package.
#   - It does NOT verify business invariants (open positions, balance,
#     etc). Always smoke-test before turning trading on.

set -euo pipefail

usage() { echo "usage: $0 <package.tar.gz> [target-dir]" >&2; exit 2; }
[ $# -ge 1 ] || usage

PKG="$1"
TARGET="${2:-quant-restored}"
[ -f "$PKG" ] || { echo "package not found: $PKG" >&2; exit 2; }

PKG_ABS="$(realpath "$PKG")"
mkdir -p "$TARGET"
cd "$TARGET"

log() { printf '\n[restore] %s\n' "$*"; }
prompt() { read -r -p "$1 [enter to continue, ctrl-c to abort] " _; }

log "1/8  extract package"
tar xzf "$PKG_ABS"
PKG_DIR=$(ls -d quant-migration-* | head -1)
[ -d "$PKG_DIR/repo" ] || tar xf "$PKG_DIR/repo.tar" -C "$PKG_DIR"

cd "$PKG_DIR/repo"

if [ ! -f .env ]; then
    log "2/8  .env missing — copying from .env.example"
    cp .env.example .env
    echo
    echo "  >>> EDIT .env NOW (exchange keys, LLM keys, alert channels, etc.) <<<"
    echo "  >>> Path: $(pwd)/.env <<<"
    prompt "    .env edited?"
else
    log "2/8  .env already present — using as-is"
fi

log "3/8  docker compose build"
docker compose build

log "4/8  start dependencies (db, redis, nats)"
docker compose up -d db redis nats
# Wait for db healthcheck
for _ in $(seq 1 60); do
    state=$(docker compose ps --format json db 2>/dev/null | grep -o '"Health":"[^"]*"' | head -1 || true)
    [[ "$state" == *healthy* ]] && break
    sleep 2
done

log "5/8  restore postgres (platform + market)"
gunzip -c "../state/postgres-platform.sql.gz" \
    | docker compose exec -T db psql -U postgres -d platform >/dev/null
gunzip -c "../state/postgres-market.sql.gz" \
    | docker compose exec -T db psql -U postgres -d market >/dev/null

log "6/8  restore redis (BGSAVE snapshot)"
docker compose stop redis
REDIS_CID=$(docker compose ps -aq redis)
docker cp "../state/redis-dump.rdb" "$REDIS_CID:/data/dump.rdb"
docker compose start redis

if [ -f "../state/grafana-data.tar.gz" ]; then
    log "7/8  restore grafana volume"
    docker volume create quant_grafana_data >/dev/null
    docker run --rm \
        -v quant_grafana_data:/data \
        -v "$(realpath ../state)":/in:ro \
        alpine sh -c "tar xzf /in/grafana-data.tar.gz -C /data"
else
    log "7/8  grafana volume tarball not in package — skipping"
fi

log "8/8  start full stack"
docker compose up -d

cat <<EOF

Restore complete. Smoke-test checklist:

  docker compose ps                                      # everything healthy
  curl -s http://localhost:8006/metrics | grep mab_      # MAB counters present
  docker compose exec redis redis-cli KEYS 'mab:*'       # arms loaded
  docker compose exec db psql -U postgres -d platform \\
    -c 'SELECT count(*) FROM crypto_decisions'           # decisions count matches source

Cut over trading only after verifying.
EOF
