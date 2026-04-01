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


def require_health(service: str, checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    overall = "ok" if all(item.get("status") == "ok" for item in checks.values()) else "error"
    payload = {"status": overall, "service": service, "checks": checks}
    if overall != "ok":
        raise RuntimeDependencyError(json.dumps(payload, default=str))
    return payload
