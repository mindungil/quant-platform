from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from nats.aio.client import Client as NATS
from nats.js.api import ConsumerConfig, StreamConfig

from shared.persistence import RedisStore


class EventEnvelope:
    def __init__(
        self,
        *,
        event_type: str,
        source: str,
        data: dict[str, Any],
        event_id: str | None = None,
        correlation_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self.event_id = event_id or str(uuid4())
        self.event_type = event_type
        self.occurred_at = datetime.now(UTC).isoformat()
        self.source = source
        self.correlation_id = correlation_id or self.event_id
        self.user_id = user_id
        self.data = data

    def model_dump(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "user_id": self.user_id,
            "data": self.data,
        }


class JetStreamBus:
    def __init__(self, *, nats_url: str, redis_store: RedisStore, enabled: bool = True) -> None:
        self._nats_url = nats_url
        self._redis = redis_store
        self._enabled = enabled
        self._client: NATS | None = None
        self._js = None

    async def connect(self) -> None:
        if not self._enabled:
            return
        self._client = NATS()
        await self._client.connect(self._nats_url)
        self._js = self._client.jetstream()

    async def close(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.drain()

    async def ensure_stream(self, name: str, subjects: list[str]) -> None:
        if self._js is None:
            return
        try:
            await self._js.add_stream(StreamConfig(name=name, subjects=subjects))
        except Exception:
            return

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        if self._js is None:
            return
        await self._js.publish(subject, json.dumps(envelope.model_dump()).encode("utf-8"))

    async def subscribe(
        self,
        *,
        stream: str,
        subject: str,
        durable: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        dlq_subject: str,
    ):
        if self._js is None:
            return None

        async def _wrapped(message) -> None:
            payload = json.loads(message.data.decode("utf-8"))
            event_id = payload["event_id"]
            idempotency_key = f"events:{durable}"
            if self._redis.sismember(idempotency_key, event_id):
                await message.ack()
                return
            try:
                await callback(payload)
                self._redis.sadd(idempotency_key, event_id)
                await message.ack()
            except Exception:
                await self.publish(
                    dlq_subject,
                    EventEnvelope(
                        event_type=f"{payload['event_type']}.dlq",
                        source=durable,
                        correlation_id=payload.get("correlation_id"),
                        user_id=payload.get("user_id"),
                        data=payload,
                    ),
                )
                await message.ack()

        return await self._js.subscribe(
            subject,
            durable=durable,
            stream=stream,
            cb=_wrapped,
            config=ConsumerConfig(deliver_policy="all"),
        )
