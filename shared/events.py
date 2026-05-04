from __future__ import annotations

import json
from asyncio import sleep
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from nats.aio.client import Client as NATS
from nats.js.api import ConsumerConfig, StreamConfig
from prometheus_client import Counter, Gauge

from shared.logging import get_logger

jetstream_messages_received_total = Counter(
    "jetstream_messages_received_total",
    "Total JetStream messages received by consumer",
    ["consumer"],
)
jetstream_messages_dlq_total = Counter(
    "jetstream_messages_dlq_total",
    "Total JetStream messages sent to DLQ",
    ["consumer"],
)
jetstream_consumer_connected = Gauge(
    "jetstream_consumer_connected",
    "JetStream consumer connection status (1=connected, 0=disconnected)",
    ["consumer"],
)
jetstream_duplicate_messages_total = Counter(
    "jetstream_duplicate_messages_total",
    "Total duplicate messages detected via idempotency check",
    ["consumer"],
)
jetstream_consumer_pending = Gauge(
    "jetstream_consumer_pending",
    "Number of pending messages for JetStream consumer",
    ["subject"],
)
jetstream_redelivery_count = Gauge(
    "jetstream_redelivery_count",
    "Number of redelivered messages for JetStream consumer",
    ["subject"],
)
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
        self.occurred_at = datetime.now(timezone.utc).isoformat()
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
        """Create or reconcile a JetStream stream.

        If the stream does not exist, create it. If it exists but its subject
        list has drifted from what the caller declared, update it in-place so
        late-added subjects become live without requiring stream deletion.
        """
        if self._js is None:
            return
        desired = set(subjects)
        for attempt in range(1, self._connect_attempts + 1):
            try:
                # Fast path: try to create. Raises if already exists.
                await self._js.add_stream(StreamConfig(name=name, subjects=subjects))
                return
            except Exception:
                # Exists (most common) or transient error. Check current
                # config and update only if subjects drifted — avoids touching
                # healthy streams on every startup.
                try:
                    info = await self._js.stream_info(name)
                    existing = set(getattr(info.config, "subjects", None) or [])
                    if existing == desired:
                        return  # Already correct
                    # Merge rather than replace — tolerates multiple services
                    # declaring overlapping-but-not-identical subject sets for
                    # the same stream.
                    merged = sorted(existing | desired)
                    await self._js.update_stream(
                        StreamConfig(name=name, subjects=merged)
                    )
                    return
                except Exception:
                    if attempt == self._connect_attempts:
                        return
                    await sleep(self._retry_delay_seconds)

    async def publish(self, subject: str, envelope: EventEnvelope) -> None:
        if self._js is None:
            return
        payload = json.dumps(envelope.model_dump()).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self._connect_attempts + 1):
            try:
                await self._js.publish(subject, payload)
                return
            except Exception as exc:
                last_error = exc
                if attempt == self._connect_attempts:
                    raise
                await sleep(self._retry_delay_seconds)
        if last_error is not None:
            raise last_error

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
            jetstream_messages_received_total.labels(consumer=durable).inc()
            try:
                info = await self._js.consumer_info(stream, durable)
                jetstream_consumer_pending.labels(subject=subject).set(info.num_pending)
                jetstream_redelivery_count.labels(subject=subject).set(info.num_redelivered)
            except Exception:
                pass
            event_id = payload.get("event_id") or str(uuid4())
            event_type = payload.get("event_type", "")

            # Guard: skip messages already on DLQ to prevent infinite .dlq.dlq... loop
            if ".dlq" in event_type or ".dlq" in message.subject:
                logger.warning(
                    "dlq_message_parked",
                    extra={"event_type": event_type, "subject": message.subject},
                )
                await message.ack()
                return

            idempotency_key = f"events:{durable}"
            if self._redis.sismember(idempotency_key, event_id):
                jetstream_duplicate_messages_total.labels(consumer=durable).inc()
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
                        "event_type": event_type,
                    },
                )
                try:
                    await self.publish(
                        dlq_subject,
                        EventEnvelope(
                            event_type=f"{event_type}.dlq",
                            source=durable,
                            correlation_id=payload.get("correlation_id"),
                            user_id=payload.get("user_id"),
                            data=payload,
                        ),
                    )
                except Exception:
                    logger.error("dlq_publish_failed", extra={"event_type": event_type})
                jetstream_messages_dlq_total.labels(consumer=durable).inc()
                await message.ack()
            finally:
                reset_request_context(tokens)

        last_error: Exception | None = None
        for attempt in range(1, self._connect_attempts + 1):
            try:
                sub = await self._js.subscribe(
                    subject,
                    durable=durable,
                    stream=stream,
                    cb=_wrapped,
                    config=ConsumerConfig(deliver_policy="all"),
                )
                jetstream_consumer_connected.labels(consumer=durable).set(1)
                return sub
            except Exception as exc:  # pragma: no cover - exercised in compose runtime
                last_error = exc
                if attempt == self._connect_attempts:
                    raise
                await sleep(self._retry_delay_seconds)
        if last_error is not None:
            raise last_error
        return None
