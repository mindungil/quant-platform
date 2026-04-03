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
from app.models.candle import CandlePayload

logger = logging.getLogger("binance_collector")

LOCAL_INGEST_BASE = os.getenv("LOCAL_INGEST_BASE", "http://127.0.0.1:8001")
MONITORED_ASSETS = os.getenv("BINANCE_COLLECTOR_ASSETS", "BTCUSDT,ETHUSDT,SOLUSDT")
INTERVAL = os.getenv("BINANCE_COLLECTOR_INTERVAL", "1h")
RECONNECT_DELAY_SECONDS = 5
MAX_RECONNECT_DELAY_SECONDS = 120


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
            else:
                logger.warning("Ingest rejected %s (status=%d)", asset, response.status_code)
    except Exception:
        logger.exception("Failed to POST candle for %s", asset)


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
    global _task
    if _task is not None:
        logger.warning("Binance collector already running")
        return
    logger.info("Starting Binance WebSocket collector background task")
    _task = asyncio.create_task(_run_ws_loop())


async def stop() -> None:
    """Cancel the background task gracefully."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    logger.info("Binance collector stopped")
