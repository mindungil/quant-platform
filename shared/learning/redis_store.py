"""Redis-backed StateStore for LearningLoop.

Drop-in replacement for InMemoryStateStore in production. Imports redis
lazily so test environments without the package can still load
`shared.learning` cleanly.
"""
from __future__ import annotations

from typing import Optional


class RedisStateStore:
    """Redis-backed implementation of the StateStore protocol."""

    def __init__(self, redis_url: str = "redis://redis:6379/0", *, socket_timeout: float = 2.0):
        import redis  # lazy
        self._r = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout,
        )

    def get(self, key: str) -> Optional[str]:
        try:
            return self._r.get(key)
        except Exception:
            return None

    def set(self, key: str, value: str) -> None:
        try:
            self._r.set(key, value)
        except Exception:
            pass

    def keys(self, pattern: str) -> list[str]:
        try:
            # SCAN is preferred over KEYS in production — use it here too.
            cursor = 0
            results: list[str] = []
            while True:
                cursor, batch = self._r.scan(cursor=cursor, match=pattern, count=500)
                results.extend(batch)
                if cursor == 0:
                    break
            return results
        except Exception:
            return []
