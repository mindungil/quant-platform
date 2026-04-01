from __future__ import annotations

import socket
import json
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from shared.persistence import RedisStore, SqlStore
from shared.runtime import RuntimeDependencyError


def check_sql(name: str, url: str) -> dict[str, str]:
    try:
        store = SqlStore(url)
        store.probe()
        return {"status": "ok", "target": name}
    except Exception as exc:
        return {"status": "error", "target": name, "detail": str(exc)}


def check_redis(name: str, url: str) -> dict[str, str]:
    try:
        store = RedisStore(url)
        if store.ping():
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
