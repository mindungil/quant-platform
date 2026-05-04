"""
NATS JetStream consumer for crypto signal threshold events.

Subscribes to ``signal.threshold.crossed.crypto`` with a durable consumer
named ``crypto-agent`` so that messages survive restarts.
"""
from __future__ import annotations

import json
import logging

from nats.aio.client import Client as NATS

from app.core.config import settings
from app.services.pipeline import run_dual_lane_pipeline
from app.services.publisher import publish_action

logger = logging.getLogger(__name__)


class CryptoAgentConsumer:
    def __init__(self) -> None:
        self._nc: NATS | None = None
        self._js = None
        self._subscription = None
        self._running: bool = False
        self._paused: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        logger.info("Consumer paused")

    def resume(self) -> None:
        self._paused = False
        logger.info("Consumer resumed")

    async def start(self) -> None:
        """Connect to NATS and subscribe via JetStream durable consumer."""
        if not settings.enable_nats:
            logger.info("NATS disabled - consumer not started")
            return

        try:
            self._nc = NATS()
            await self._nc.connect(settings.nats_url)
        except Exception:
            logger.exception("Failed to connect to NATS at %s", settings.nats_url)
            return

        try:
            self._js = self._nc.jetstream()
            try:
                from nats.js.api import StreamConfig  # noqa: E402
                await self._js.add_stream(
                    StreamConfig(
                        name=settings.nats_stream,
                        subjects=["signal.threshold.crossed.*", "agent.crypto.*"],
                    ),
                )
                logger.info("Created JetStream stream '%s'", settings.nats_stream)
            except Exception:
                logger.warning(
                    "Could not create/find stream '%s' - falling back to plain subscription",
                    settings.nats_stream,
                )

            self._subscription = await self._js.subscribe(
                settings.nats_subscribe_subject,
                durable=settings.nats_durable_name,
                cb=self._handle,
            )
            logger.info(
                "Subscribed to '%s' (durable=%s) via JetStream",
                settings.nats_subscribe_subject,
                settings.nats_durable_name,
            )
        except Exception:
            # Fall back to plain NATS subscription
            self._subscription = await self._nc.subscribe(
                settings.nats_subscribe_subject,
                cb=self._handle,
            )
            logger.info(
                "Subscribed to '%s' via plain NATS (no JetStream)",
                settings.nats_subscribe_subject,
            )

        self._running = True

    async def stop(self) -> None:
        """Gracefully unsubscribe and drain the NATS connection."""
        self._running = False
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            except Exception:
                logger.exception("Error unsubscribing from NATS")
        if self._nc is not None and self._nc.is_connected:
            try:
                await self._nc.drain()
            except Exception:
                logger.exception("Error draining NATS connection")

    async def _handle(self, message) -> None:
        """Process an incoming threshold-crossed message."""
        if self._paused:
            logger.debug("Consumer paused - ignoring message")
            if hasattr(message, "ack"):
                await message.ack()
            return

        try:
            payload = json.loads(message.data.decode("utf-8"))
            asset = payload.get("asset")
            if not asset:
                logger.warning("Received message with no 'asset' field: %s", payload)
                if hasattr(message, "ack"):
                    await message.ack()
                return

            logger.info("Processing threshold event for %s (dual-lane)", asset)
            states = await run_dual_lane_pipeline(asset)
            # Publish one action event per lane run so downstream (frontend
            # stream, performance) can attribute by lane.
            for state in states:
                await publish_action(state, nc=self._nc)
            if hasattr(message, "ack"):
                await message.ack()
        except Exception:
            logger.exception("Error handling NATS message")
            if hasattr(message, "nak"):
                await message.nak()


consumer = CryptoAgentConsumer()
