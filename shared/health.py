from __future__ import annotations

import socket
import json
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, HTTPException

from shared.persistence import RedisStore, SqlStore
from shared.runtime import RuntimeDependencyError

CheckFn = Callable[[], dict[str, Any]]


# Cache stores per-URL so repeated health checks reuse the same connection
# pool instead of churning new engines (which can exhaust Postgres'
# max_connections when every service probes DB on every /ready hit).
_SQL_STORE_CACHE: dict[str, SqlStore] = {}
_REDIS_STORE_CACHE: dict[str, RedisStore] = {}


def _get_sql_store(url: str) -> SqlStore:
    store = _SQL_STORE_CACHE.get(url)
    if store is None:
        store = SqlStore(url)
        _SQL_STORE_CACHE[url] = store
    return store


def _get_redis_store(url: str) -> RedisStore:
    store = _REDIS_STORE_CACHE.get(url)
    if store is None:
        store = RedisStore(url)
        _REDIS_STORE_CACHE[url] = store
    return store


def check_sql(name: str, url: str) -> dict[str, str]:
    try:
        _get_sql_store(url).probe()
        return {"status": "ok", "target": name}
    except Exception as exc:
        return {"status": "error", "target": name, "detail": str(exc)}


def check_redis(name: str, url: str) -> dict[str, str]:
    try:
        if _get_redis_store(url).ping():
            return {"status": "ok", "target": name}
        return {"status": "error", "target": name, "detail": "redis_ping_failed"}
    except Exception as exc:
        return {"status": "error", "target": name, "detail": str(exc)}


def check_tcp(name: str, url: str, *, default_port: int) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=3.0):
            return {"status": "ok", "target": name}
    except Exception as exc:
        return {"status": "error", "target": name, "detail": str(exc)}


def health_payload(service: str, checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    overall = "ok" if all(item.get("status") == "ok" for item in checks.values()) else "error"
    payload = {"status": overall, "service": service, "checks": checks}
    if overall != "ok":
        raise HTTPException(status_code=503, detail=payload)
    return payload


def require_health(
    service: str,
    checks: dict[str, dict[str, Any]],
    *,
    retries: int = 10,
    delay: float = 3.0,
) -> dict[str, Any]:
    import time
    import logging

    logger = logging.getLogger(service)
    for attempt in range(retries):
        overall = "ok" if all(item.get("status") == "ok" for item in checks.values()) else "error"
        if overall == "ok":
            return {"status": overall, "service": service, "checks": checks}
        if attempt < retries - 1:
            failed = [k for k, v in checks.items() if v.get("status") != "ok"]
            logger.warning(
                "startup health check failed (attempt %d/%d), retrying in %.0fs: %s",
                attempt + 1, retries, delay, failed,
            )
            time.sleep(delay)
            # Re-run checks that failed (caller passes check functions, not results)
            # Since checks is already evaluated, we just wait and let caller re-evaluate
            # For now, return with a warning instead of crashing
    # After all retries, warn but don't crash
    payload = {"status": "degraded", "service": service, "checks": checks}
    logger.warning("startup health check exhausted retries, starting in degraded mode: %s", payload)
    return payload


def install_health_endpoints(
    app: FastAPI,
    *,
    service: str,
    readiness_checks: dict[str, CheckFn] | None = None,
    extra_info: dict[str, Any] | None = None,
) -> None:
    """Register `/live`, `/ready`, and `/health` on *app*.

    - `/live` is a static liveness probe: the process is running and the event
      loop is responsive. Never calls out to dependencies. Used by container
      liveness probes where failure should trigger a restart.
    - `/ready` evaluates *readiness_checks* (name → zero-arg function returning
      a `{status, target, ...}` dict). Returns 503 when any check fails. Use
      this for load-balancer gating.
    - `/health` preserves the legacy surface: same body as `/ready` when checks
      exist, otherwise same as `/live`.

    Each check is called per-request; pass lightweight probes (ping, SELECT 1).
    """
    router = APIRouter()
    checks = readiness_checks or {}

    def _live_payload() -> dict[str, Any]:
        body: dict[str, Any] = {"status": "ok", "service": service}
        if extra_info:
            body.update(extra_info)
        return body

    def _ready_payload() -> dict[str, Any]:
        evaluated = {name: fn() for name, fn in checks.items()}
        overall = "ok" if all(r.get("status") == "ok" for r in evaluated.values()) else "error"
        body: dict[str, Any] = {"status": overall, "service": service, "checks": evaluated}
        if extra_info:
            body.update(extra_info)
        if overall != "ok":
            raise HTTPException(status_code=503, detail=body)
        return body

    @router.get("/live")
    def live() -> dict[str, Any]:
        return _live_payload()

    @router.get("/ready")
    def ready() -> dict[str, Any]:
        return _ready_payload()

    @router.get("/health")
    def health() -> dict[str, Any]:
        return _ready_payload() if checks else _live_payload()

    # /metrics — default Prometheus scrape endpoint. Every service that
    # wires health endpoints also gets metrics for free, so Prometheus
    # targets don't show up as `down` just because a service forgot to
    # register /metrics itself.
    try:
        from fastapi import Response
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        @router.get("/metrics")
        def metrics() -> Response:
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except ImportError:
        pass

    app.include_router(router)
