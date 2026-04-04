"""Binance WebSocket real-time kline (candlestick) collector.

Connects to Binance public WebSocket stream for BTCUSDT 1h candles.
When a candle closes (kline.x == true), posts the data to the local
ingestion API endpoint ``POST /candles/BTCUSDT``.

Controlled via environment variable ``ENABLE_BINANCE_COLLECTOR``.
Defaults to ``false`` so that it must be opted-in explicitly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime

import websockets
import websockets.exceptions
from prometheus_client import Counter

from app.models.candle import CandlePayload
from shared.events import EventEnvelope, JetStreamBus
from shared.persistence import RedisStore

logger = logging.getLogger("binance_collector")

candle_ingest_total = Counter(
    "candle_ingest_total",
    "Total candle ingestion attempts",
    ["asset", "status"],
)

LOCAL_INGEST_BASE = os.getenv("LOCAL_INGEST_BASE", "http://127.0.0.1:8001")
MONITORED_ASSETS = os.getenv("BINANCE_COLLECTOR_ASSETS", "BTCUSDT,ETHUSDT,SOLUSDT")
INTERVAL = os.getenv("BINANCE_COLLECTOR_INTERVAL", "1h")
RECONNECT_DELAY_SECONDS = 5
MAX_RECONNECT_DELAY_SECONDS = 120
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_event_bus: JetStreamBus | None = None


def _build_ws_url(assets: list[str], interval: str) -> str:
    """Build combined stream URL for multiple assets."""
    streams = "/".join(f"{a.lower()}@kline_{interval}" for a in assets)
    return f"wss://stream.binance.com:9443/stream?streams={streams}"


def is_enabled() -> bool:
    return os.getenv("ENABLE_BINANCE_COLLECTOR", "true").lower() == "true"


def _kline_to_candle(kline: dict) -> CandlePayload:
    """Convert a Binance kline payload to a ``CandlePayload``."""
    return CandlePayload(
        timestamp=datetime.fromtimestamp(kline["t"] / 1000, tz=UTC),
        open=float(kline["o"]),
        high=float(kline["h"]),
        low=float(kline["l"]),
        close=float(kline["c"]),
        volume=float(kline["v"]),
    )


async def _post_candle(asset: str, candle: CandlePayload) -> None:
    """POST a closed candle to the local market-data ingestion API."""
    import httpx

    url = f"{LOCAL_INGEST_BASE}/candles/{asset}"
    payload = candle.model_dump(mode="json")
    payload["timestamp"] = candle.timestamp.isoformat()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code < 300:
                logger.info("Ingested candle %s at %s", asset, candle.timestamp.isoformat())
                candle_ingest_total.labels(asset=asset, status="success").inc()
                # Publish NATS event for downstream consumers
                if _event_bus is not None:
                    try:
                        await _event_bus.publish(
                            "market.candle.ingested",
                            EventEnvelope(
                                event_type="market.candle.ingested",
                                source="binance-collector",
                                data={
                                    "asset": asset,
                                    "timestamp": candle.timestamp.isoformat(),
                                    "close": candle.close,
                                    "volume": candle.volume,
                                },
                            ),
                        )
                    except Exception as pub_exc:
                        logger.warning("Failed to publish candle event: %s", pub_exc)
            else:
                logger.warning("Ingest rejected %s (status=%d)", asset, response.status_code)
                candle_ingest_total.labels(asset=asset, status="failed").inc()
    except Exception:
        logger.exception("Failed to POST candle for %s", asset)
        candle_ingest_total.labels(asset=asset, status="failed").inc()


async def _run_ws_loop() -> None:
    """Multi-asset WebSocket loop with exponential-backoff reconnection."""
    assets = [a.strip() for a in MONITORED_ASSETS.split(",") if a.strip()]
    if not assets:
        logger.warning("No assets configured for Binance collector")
        return

    ws_url = _build_ws_url(assets, INTERVAL)
    delay = RECONNECT_DELAY_SECONDS

    while True:
        try:
            logger.info("Connecting to Binance WebSocket for %d assets: %s", len(assets), assets)
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                delay = RECONNECT_DELAY_SECONDS
                logger.info("Connected to Binance combined stream")
                async for raw_message in ws:
                    try:
                        message = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    # Combined stream wraps data in {"stream": "...", "data": {...}}
                    data = message.get("data", message)
                    kline = data.get("k")
                    if kline is None:
                        continue

                    if not kline.get("x", False):
                        continue

                    # Extract asset from stream name or kline symbol
                    asset = kline.get("s", "").upper()
                    if not asset:
                        stream = message.get("stream", "")
                        asset = stream.split("@")[0].upper() if "@" in stream else "UNKNOWN"

                    logger.info("Candle closed: %s at kline.t=%s", asset, kline.get("t"))
                    try:
                        candle = _kline_to_candle(kline)
                        await _post_candle(asset, candle)
                    except Exception:
                        logger.exception("Error processing kline for %s", asset)

        except asyncio.CancelledError:
            logger.info("Binance collector task cancelled, shutting down")
            return
        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.InvalidURI,
            OSError,
        ) as exc:
            logger.warning(
                "WebSocket disconnected (%s). Reconnecting in %ds...",
                exc,
                delay,
            )
        except Exception:
            logger.exception(
                "Unexpected error in Binance collector. Reconnecting in %ds...",
                delay,
            )

        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)


_task: asyncio.Task[None] | None = None


async def start() -> None:
    """Launch the collector as a background asyncio task."""
    global _task, _event_bus
    if _task is not None:
        logger.warning("Binance collector already running")
        return
    # Initialize NATS event bus for publishing candle events
    try:
        _event_bus = JetStreamBus(
            nats_url=NATS_URL,
            redis_store=RedisStore(REDIS_URL),
            enabled=True,
        )
        await _event_bus.connect()
        await _event_bus.ensure_stream("MARKET", ["market.>"])
        logger.info("Binance collector NATS event bus connected")
    except Exception as exc:
        logger.warning("Binance collector NATS init failed (events disabled): %s", exc)
        _event_bus = None
    logger.info("Starting Binance WebSocket collector background task")
    _task = asyncio.create_task(_run_ws_loop())


async def stop() -> None:
    """Cancel the background task gracefully."""
    global _task, _event_bus
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    if _event_bus is not None:
        await _event_bus.close()
        _event_bus = None
    logger.info("Binance collector stopped")
