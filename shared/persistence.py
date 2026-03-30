from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@dataclass
class SqlStore:
    url: str
    engine: Engine | None = None
    available: bool = True

    def __post_init__(self) -> None:
        self.engine = create_engine(self.url, future=True, pool_pre_ping=True)

    @contextmanager
    def connection(self):
        assert self.engine is not None
        with self.engine.begin() as connection:
            yield connection

    def fetch_all(self, query: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        try:
            with self.connection() as connection:
                rows = connection.execute(text(query), values or {}).mappings().all()
            self.available = True
            return [dict(row) for row in rows]
        except Exception:
            self.available = False
            return []

    def fetch_one(self, query: str, values: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.fetch_all(query, values)
        return rows[0] if rows else None

    def execute(self, query: str, values: dict[str, Any] | None = None) -> None:
        try:
            with self.connection() as connection:
                connection.execute(text(query), values or {})
            self.available = True
        except Exception:
            self.available = False


class RedisStore:
    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._fallback_hashes: dict[str, dict[str, str]] = {}
        self._fallback_sets: dict[str, set[str]] = {}

    def hset_json(self, key: str, field: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, default=str)
        try:
            self._client.hset(key, field, payload)
        except Exception:
            self._fallback_hashes.setdefault(key, {})[field] = payload

    def hget_json(self, key: str, field: str) -> dict[str, Any] | None:
        try:
            value = self._client.hget(key, field)
        except Exception:
            value = self._fallback_hashes.get(key, {}).get(field)
        if value is None:
            return None
        return json.loads(value)

    def sadd(self, key: str, value: str) -> None:
        try:
            self._client.sadd(key, value)
        except Exception:
            self._fallback_sets.setdefault(key, set()).add(value)

    def sismember(self, key: str, value: str) -> bool:
        try:
            return bool(self._client.sismember(key, value))
        except Exception:
            return value in self._fallback_sets.get(key, set())

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


def serialize_json(value: dict[str, Any]) -> str:
    return json.dumps(value, default=str)


def deserialize_json(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return json.loads(value)


def now_iso() -> str:
    return datetime.utcnow().isoformat()
