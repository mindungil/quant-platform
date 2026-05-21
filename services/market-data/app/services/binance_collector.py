"""Binance REST-polling kline collector.

Polls Binance ``GET /api/v3/klines`` for the most recent closed candle of each
monitored asset. When a new closed candle appears, posts it to the local
ingestion API endpoint ``POST /candles/{asset}``.

History (G1): the original implementation used ``wss://stream.binance.com``
WebSocket streams. The container egress can complete TCP to that host but the
TLS handshake to the WS endpoints (both :9443 and :443) hangs indefinitely,
while the REST host (``api.binance.com:443``) handshakes normally and returns
200 within 200ms. REST polling sidesteps the WS-edge filtering entirely.

Controlled via environment variable ``ENABLE_BINANCE_COLLECTOR``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

UTC = timezone.utc

import httpx
from prometheus_client import Counter

from app.models.candle import CandlePayload
from shared.events import JetStreamBus

logger = logging.getLogger("binance_collector")

candle_ingest_total = Counter(
    "candle_ingest_total",
    "Total candle ingestion attempts",
    ["asset", "status"],
)

LOCAL_INGEST_BASE = os.getenv("LOCAL_INGEST_BASE", "http://127.0.0.1:8001")
BINANCE_REST_BASE = os.getenv("BINANCE_API_BASE_URL", "https://api.binance.com")
MONITORED_ASSETS = os.getenv("BINANCE_COLLECTOR_ASSETS", "BTCUSDT,ETHUSDT,SOLUSDT")
INTERVAL = os.getenv("BINANCE_COLLECTOR_INTERVAL", "1m")
POLL_INTERVAL_SECONDS = float(os.getenv("BINANCE_COLLECTOR_POLL_SECONDS", "30"))
RECONNECT_DELAY_SECONDS = 5
MAX_RECONNECT_DELAY_SECONDS = 120
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_event_bus: JetStreamBus | None = None


def is_enabled() -> bool:
    return os.getenv("ENABLE_BINANCE_COLLECTOR", "true").lower() == "true"


def _kline_row_to_candle(row: list) -> CandlePayload:
    """Convert a Binance REST kline row to a ``CandlePayload``.

    Schema: [open_time_ms, open, high, low, close, volume, close_time_ms, ...]
    """
    return CandlePayload(
        timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


async def _post_candle(asset: str, candle: CandlePayload) -> None:
    """POST a closed candle to the local market-data ingestion API."""
    url = f"{LOCAL_INGEST_BASE}/candles/{asset}"
    payload = candle.model_dump(mode="json")
    payload["timestamp"] = candle.timestamp.isoformat()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code < 300:
                logger.info("Ingested candle %s at %s", asset, candle.timestamp.isoformat())
                candle_ingest_total.labels(asset=asset, status="success").inc()
            else:
                logger.warning("Ingest rejected %s (status=%d)", asset, response.status_code)
                candle_ingest_total.labels(asset=asset, status="failed").inc()
    except Exception:
        logger.exception("Failed to POST candle for %s", asset)
        candle_ingest_total.labels(asset=asset, status="failed").inc()


async def _fetch_klines(client: httpx.AsyncClient, asset: str, interval: str) -> list[list] | None:
    """Fetch the last 2 klines for an asset. Returns None on error."""
    url = f"{BINANCE_REST_BASE}/api/v3/klines"
    params = {"symbol": asset, "interval": interval, "limit": 2}
    try:
        response = await client.get(url, params=params, timeout=10.0)
        if response.status_code != 200:
            logger.warning("Binance klines %s status=%d body=%s", asset, response.status_code, response.text[:200])
            return None
        return response.json()
    except Exception:
        logger.exception("Binance klines fetch failed for %s", asset)
        return None


async def _run_poll_loop() -> None:
    """REST polling loop. Per-asset last-seen close_time prevents duplicate ingest."""
    assets = [a.strip() for a in MONITORED_ASSETS.split(",") if a.strip()]
    if not assets:
        logger.warning("No assets configured for Binance collector")
        return

    last_close_ms: dict[str, int] = {}
    delay = RECONNECT_DELAY_SECONDS

    logger.info(
        "Starting Binance REST poll loop: assets=%s interval=%s poll_every=%ss base=%s",
        assets, INTERVAL, POLL_INTERVAL_SECONDS, BINANCE_REST_BASE,
    )

    async with httpx.AsyncClient() as client:
        while True:
            try:
                for asset in assets:
                    klines = await _fetch_klines(client, asset, INTERVAL)
                    if not klines or len(klines) < 1:
                        continue

                    # klines is oldest→newest. The last entry may be the
                    # in-progress (still-forming) candle; the one before it is
                    # the most recently closed. Use whichever is genuinely
                    # closed: a candle is closed when close_time_ms < now_ms.
                    now_ms = int(datetime.now(UTC).timestamp() * 1000)
                    closed_row = None
                    for row in reversed(klines):
                        if row[6] < now_ms:
                            closed_row = row
                            break
                    if closed_row is None:
                        continue

                    close_ms = closed_row[6]
                    if close_ms <= last_close_ms.get(asset, 0):
                        continue  # already ingested

                    try:
                        candle = _kline_row_to_candle(closed_row)
                        logger.info("Candle closed: %s at kline.t=%s", asset, closed_row[0])
                        await _post_candle(asset, candle)
                        last_close_ms[asset] = close_ms
                    except Exception:
                        logger.exception("Error processing kline for %s", asset)

                # successful pass — reset backoff
                delay = RECONNECT_DELAY_SECONDS

            except asyncio.CancelledError:
                logger.info("Binance collector task cancelled, shutting down")
                return
            except Exception:
                logger.exception(
                    "Unexpected error in Binance collector. Backing off %ds...",
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)
                continue

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


_task: asyncio.Task[None] | None = None


async def start() -> None:
    """Launch the collector as a background asyncio task."""
    global _task, _event_bus
    if _task is not None:
        logger.warning("Binance collector already running")
        return
    # Collector no longer publishes directly to NATS — the HTTP ingest route
    # owns the market.candle.updated.* publish.
    _event_bus = None
    logger.info("Starting Binance REST poll collector background task")
    _task = asyncio.create_task(_run_poll_loop())


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
