#!/usr/bin/env bash
# Package the running quant stack into a self-contained migration tarball.
#
#   ./scripts/ops/package-migration.sh [--include-prometheus]
#
# Output: ./migration/quant-migration-<ts>.tar.gz containing:
#   repo.tar                    git archive of HEAD (source + compose)
#   state/postgres-platform.sql.gz
#   state/postgres-market.sql.gz
#   state/redis-dump.rdb        Redis BGSAVE snapshot (MAB arms + transient keys)
#   state/grafana-data.tar.gz   Grafana volume contents (if running)
#   state/prometheus-data.tar.gz  (only with --include-prometheus; often huge)
#   RESTORE.md
#
# What is NOT included:
#   - .env (secrets) — transfer separately via a secure channel
#   - prometheus tsdb (skip by default — metrics history is rebuildable)
#
# Pre-flight: source stack must be running. The script holds no locks; new
# writes during dump end up on the source only — drain trading first if you
# want a clean cutover.

set -euo pipefail

INCLUDE_PROM=0
for arg in "$@"; do
    case "$arg" in
        --include-prometheus) INCLUDE_PROM=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

TS=$(date -u +%Y%m%d-%H%M%S)
PKG_NAME="quant-migration-$TS"
STAGE="migration/$PKG_NAME"
mkdir -p "$STAGE/state"

log() { printf '\n[package] %s\n' "$*"; }

log "1/6  git archive (source + compose)"
git archive HEAD --prefix=repo/ -o "$STAGE/repo.tar"

log "2/6  pg_dump platform"
docker compose exec -T db pg_dump -U postgres --no-owner --clean --if-exists platform \
    | gzip -9 > "$STAGE/state/postgres-platform.sql.gz"

log "3/6  pg_dump market"
docker compose exec -T db pg_dump -U postgres --no-owner --clean --if-exists market \
    | gzip -9 > "$STAGE/state/postgres-market.sql.gz"

log "4/6  redis BGSAVE + dump.rdb extract"
docker compose exec -T redis redis-cli BGSAVE >/dev/null
# Wait for last_save to bump past the previous value
prev=$(docker compose exec -T redis redis-cli LASTSAVE)
for _ in $(seq 1 30); do
    cur=$(docker compose exec -T redis redis-cli LASTSAVE)
    [ "$cur" != "$prev" ] && break
    sleep 1
done
REDIS_CID=$(docker compose ps -q redis)
docker cp "$REDIS_CID:/data/dump.rdb" "$STAGE/state/redis-dump.rdb"

log "5/6  grafana volume"
if docker volume inspect quant_grafana_data >/dev/null 2>&1; then
    docker run --rm \
        -v quant_grafana_data:/data:ro \
        -v "$REPO_ROOT/$STAGE/state":/out \
        alpine sh -c "tar czf /out/grafana-data.tar.gz -C /data ."
else
    log "     (skipped — quant_grafana_data volume not present)"
fi

if [ "$INCLUDE_PROM" = "1" ] && docker volume inspect quant_prometheus_data >/dev/null 2>&1; then
    log "6/6  prometheus tsdb (large)"
    docker run --rm \
        -v quant_prometheus_data:/data:ro \
        -v "$REPO_ROOT/$STAGE/state":/out \
        alpine sh -c "tar czf /out/prometheus-data.tar.gz -C /data ."
else
    log "6/6  prometheus tsdb skipped (use --include-prometheus to include)"
fi

cat > "$STAGE/RESTORE.md" <<'EOF'
# Restoring this migration package

Target host requirements:
  - Ubuntu (any recent LTS)
  - docker + docker compose v2

Steps (run `restore-migration.sh` for the automated path, or follow manually):

1. Extract the tarball and `cd` into it.
2. Untar the repo:  `tar xf repo.tar && cd repo`
3. Copy `.env.example` to `.env` and fill in secrets (exchange API keys,
   LLM keys, alert channels, etc). NEVER reuse secrets across instances —
   rotate them.
4. Build images:  `docker compose build`
5. Start dependencies only:  `docker compose up -d db redis nats`
6. Wait until db is healthy:  `docker compose ps db`
7. Restore Postgres:
       gunzip -c ../state/postgres-platform.sql.gz \
         | docker compose exec -T db psql -U postgres -d platform
       gunzip -c ../state/postgres-market.sql.gz \
         | docker compose exec -T db psql -U postgres -d market
8. Restore Redis (must stop container first since RDB is loaded at start):
       docker compose stop redis
       docker cp ../state/redis-dump.rdb $(docker compose ps -q redis):/data/dump.rdb
       docker compose start redis
9. (Optional) Restore Grafana volume similarly with the tar.gz.
10. Start the full stack:  `docker compose up -d`
11. Smoke-test: hit `/metrics` on intelligence, check MAB state in Redis,
    check `crypto_decisions` row count matches source within tolerance.

Cutover note: anything traded on the source between snapshot time and
target start is lost. Drain trading on source before packaging if zero
data loss matters.
EOF

log "bundling tarball"
cd migration
tar czf "$PKG_NAME.tar.gz" "$PKG_NAME"
SIZE=$(du -h "$PKG_NAME.tar.gz" | cut -f1)
log "done -> migration/$PKG_NAME.tar.gz  ($SIZE)"
log "stage dir kept at: migration/$PKG_NAME/  (remove when verified)"
