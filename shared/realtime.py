from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from shared.persistence import RedisStore


class RealtimeBus:
    def __init__(self, redis_store: RedisStore, *, replay_limit: int = 200) -> None:
        self._redis = redis_store
        self._replay_limit = replay_limit

    def _event_key(self, user_id: str | None) -> str:
        return "realtime:events:global" if user_id is None else f"realtime:events:user:{user_id}"

    def _channel(self, user_id: str | None) -> str:
        return "realtime:global" if user_id is None else f"realtime:user:{user_id}"

    def publish(
        self,
        *,
        event_type: str,
        source: str,
        data: dict[str, Any],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": str(uuid4()),
            "type": event_type,
            "source": source,
            "occurred_at": datetime.now(UTC).isoformat(),
            "user_id": user_id,
            "data": data,
        }
        self._redis.lpush_json(self._event_key(None), event, max_items=self._replay_limit)
        self._redis.publish_json(self._channel(None), event)
        if user_id is not None:
            self._redis.lpush_json(self._event_key(user_id), event, max_items=self._replay_limit)
            self._redis.publish_json(self._channel(user_id), event)
        return event

    def recent(self, *, user_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        events = self._redis.lrange_json(self._event_key(None), 0, limit - 1)
        if user_id is not None:
            events.extend(self._redis.lrange_json(self._event_key(user_id), 0, limit - 1))
        deduped = {event["event_id"]: event for event in events}
        return sorted(deduped.values(), key=lambda event: event["occurred_at"])[-limit:]

    async def subscribe(self, *, user_id: str | None = None):
        channels = [self._channel(None)]
        if user_id is not None:
            channels.append(self._channel(user_id))
        return await self._redis.subscribe(*channels)
