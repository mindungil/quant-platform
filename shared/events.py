from __future__ import annotations

import json
from asyncio import sleep
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from nats.aio.client import Client as NATS
from nats.js.api import ConsumerConfig, StreamConfig

from shared.logging import get_logger
from shared.persistence import RedisStore
from shared.request_context import reset_request_context, set_request_context

logger = get_logger("shared-events")


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
    def __init__(
        self,
        *,
        nats_url: str,
        redis_store: RedisStore,
        enabled: bool = True,
        connect_attempts: int = 15,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        self._nats_url = nats_url
        self._redis = redis_store
        self._enabled = enabled
        self._connect_attempts = connect_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._client: NATS | None = None
        self._js = None

    async def connect(self) -> None:
        if not self._enabled:
            return
        if self._client is not None and self._client.is_connected:
            return
        last_error: Exception | None = None
        for attempt in range(1, self._connect_attempts + 1):
            try:
                self._client = NATS()
                await self._client.connect(self._nats_url)
                self._js = self._client.jetstream()
                return
            except Exception as exc:  # pragma: no cover - exercised in compose runtime
                last_error = exc
                if attempt == self._connect_attempts:
                    raise
                await sleep(self._retry_delay_seconds)
        if last_error is not None:
            raise last_error

    async def close(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.drain()

    async def ensure_stream(self, name: str, subjects: list[str]) -> None:
        if self._js is None:
            return
        for attempt in range(1, self._connect_attempts + 1):
            try:
                await self._js.add_stream(StreamConfig(name=name, subjects=subjects))
                return
            except Exception:
                if attempt == self._connect_attempts:
                    return
                await sleep(self._retry_delay_seconds)

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
            tokens = set_request_context(
                request_id=event_id,
                correlation_id=payload.get("correlation_id") or event_id,
                user_id=payload.get("user_id"),
            )
            try:
                await callback(payload)
                self._redis.sadd(idempotency_key, event_id)
                await message.ack()
            except Exception as exc:
                logger.exception(
                    "jetstream_consumer_failed",
                    extra={
                        "service": "shared-events",
                        "correlation_id": payload.get("correlation_id"),
                        "user_id": payload.get("user_id"),
                        "event_type": payload.get("event_type"),
                    },
                )
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
            finally:
                reset_request_context(tokens)

        last_error: Exception | None = None
        for attempt in range(1, self._connect_attempts + 1):
            try:
                return await self._js.subscribe(
                    subject,
                    durable=durable,
                    stream=stream,
                    cb=_wrapped,
                    config=ConsumerConfig(deliver_policy="all"),
                )
            except Exception as exc:  # pragma: no cover - exercised in compose runtime
                last_error = exc
                if attempt == self._connect_attempts:
                    raise
                await sleep(self._retry_delay_seconds)
        if last_error is not None:
            raise last_error
        return None
