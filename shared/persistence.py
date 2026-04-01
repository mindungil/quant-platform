from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis
import redis.asyncio as aioredis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from shared.runtime import RuntimeDependencyError, strict_runtime_enabled


@dataclass
class SqlStore:
    url: str
    engine: Engine | None = None
    available: bool = True

    def __post_init__(self) -> None:
        self.engine = create_engine(self.url, future=True, pool_pre_ping=True)
        if strict_runtime_enabled():
            self.probe()

    @contextmanager
    def connection(self):
        assert self.engine is not None
        with self.engine.begin() as connection:
            yield connection

    def probe(self) -> None:
        try:
            with self.connection() as connection:
                connection.execute(text("SELECT 1"))
            self.available = True
        except Exception as exc:
            self.available = False
            if strict_runtime_enabled():
                raise RuntimeDependencyError(f"sql_unavailable:{self.url}") from exc

    def fetch_all(self, query: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            with self.connection() as connection:
                rows = connection.execute(text(query), values or {}).mappings().all()
            self.available = True
            return [dict(row) for row in rows]
        except Exception as exc:
            self.available = False
            if strict_runtime_enabled():
                raise RuntimeDependencyError("sql_query_failed") from exc
            return []

    def fetch_one(self, query: str, values: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.fetch_all(query, values)
        return rows[0] if rows else None

    def execute(self, query: str, values: dict[str, Any] | None = None) -> None:
        try:
            with self.connection() as connection:
                connection.execute(text(query), values or {})
            self.available = True
        except Exception as exc:
            self.available = False
            if strict_runtime_enabled():
                raise RuntimeDependencyError("sql_execute_failed") from exc


class RedisStore:
    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._async_client = aioredis.from_url(url, decode_responses=True)
        self._fallback_hashes: dict[str, dict[str, str]] = {}
        self._fallback_sets: dict[str, set[str]] = {}
        self._fallback_lists: dict[str, list[str]] = {}
        if strict_runtime_enabled():
            self.require_ping()

    def require_ping(self) -> None:
        try:
            if not self._client.ping():
                raise RuntimeDependencyError("redis_ping_failed")
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_unavailable") from exc

    def hset_json(self, key: str, field: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, default=str)
        try:
            self._client.hset(key, field, payload)
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_hset_failed") from exc
            self._fallback_hashes.setdefault(key, {})[field] = payload

    def hget_json(self, key: str, field: str) -> dict[str, Any] | None:
        try:
            value = self._client.hget(key, field)
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_hget_failed") from exc
            value = self._fallback_hashes.get(key, {}).get(field)
        if value is None:
            return None
        return json.loads(value)

    def sadd(self, key: str, value: str) -> None:
        try:
            self._client.sadd(key, value)
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_sadd_failed") from exc
            self._fallback_sets.setdefault(key, set()).add(value)

    def sismember(self, key: str, value: str) -> bool:
        try:
            return bool(self._client.sismember(key, value))
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_sismember_failed") from exc
            return value in self._fallback_sets.get(key, set())

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def lpush_json(self, key: str, value: dict[str, Any], max_items: int | None = None) -> None:
        payload = json.dumps(value, default=str)
        try:
            self._client.lpush(key, payload)
            if max_items is not None:
                self._client.ltrim(key, 0, max_items - 1)
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_lpush_failed") from exc
            items = self._fallback_lists.setdefault(key, [])
            items.insert(0, payload)
            if max_items is not None:
                del items[max_items:]

    def lrange_json(self, key: str, start: int, stop: int) -> list[dict[str, Any]]:
        try:
            values = self._client.lrange(key, start, stop)
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_lrange_failed") from exc
            items = self._fallback_lists.get(key, [])
            values = items[start : stop + 1 if stop >= 0 else None]
        return [json.loads(value) for value in values]

    def publish_json(self, channel: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, default=str)
        try:
            self._client.publish(channel, payload)
        except Exception as exc:
            if strict_runtime_enabled():
                raise RuntimeDependencyError("redis_publish_failed") from exc
            return

    async def close_async(self) -> None:
        try:
            await self._async_client.aclose()
        except Exception:
            return

    async def subscribe(self, *channels: str):
        pubsub = self._async_client.pubsub()
        await pubsub.subscribe(*channels)
        return pubsub


def serialize_json(value: Any) -> str:
    return json.dumps(value, default=str)


def deserialize_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    return json.loads(value)


def now_iso() -> str:
    return datetime.utcnow().isoformat()
