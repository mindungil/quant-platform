"""Upbit WebSocket real-time ticker collector with local candle aggregation.

Connects to Upbit public WebSocket stream for KRW-BTC, KRW-ETH, KRW-SOL
ticker updates. Aggregates ticker data into 1-minute OHLCV candles locally.
When a minute boundary is crossed, posts the completed candle to the local
ingestion API endpoint ``POST /candles/{asset}``.

Controlled via environment variable ``ENABLE_UPBIT_COLLECTOR``.
Defaults to ``false`` so that it must be opted-in explicitly.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import uuid
from datetime import datetime, timezone

UTC = timezone.utc

import websockets
import websockets.exceptions
from prometheus_client import Counter

from app.models.candle import CandlePayload
from shared.events import EventEnvelope, JetStreamBus
from shared.persistence import RedisStore

logger = logging.getLogger("upbit_collector")

candle_ingest_total = Counter(
    "upbit_candle_ingest_total",
    "Total Upbit candle ingestion attempts",
    ["asset", "status"],
)

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
LOCAL_INGEST_BASE = os.getenv("LOCAL_INGEST_BASE", "http://127.0.0.1:8001")
MONITORED_ASSETS = os.getenv("UPBIT_COLLECTOR_ASSETS", "KRW-BTC,KRW-ETH,KRW-SOL")
RECONNECT_DELAY_SECONDS = 5
MAX_RECONNECT_DELAY_SECONDS = 120
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_event_bus: JetStreamBus | None = None


class _CandleAggregator:
    """Aggregates ticker updates into 1-minute OHLCV candles."""

    def __init__(self) -> None:
        # Keyed by (asset, minute_timestamp_str)
        self._candles: dict[str, dict] = {}

    def _minute_key(self, dt: datetime) -> str:
        """Return a string key for the minute bucket."""
        return dt.strftime("%Y-%m-%dT%H:%M:00")

    def update(self, asset: str, price: float, volume: float, trade_time: datetime) -> CandlePayload | None:
        """Process a ticker update.

        Returns a completed CandlePayload if the trade crossed into a new
        minute boundary, otherwise None.
        """
        minute_key = self._minute_key(trade_time)
        candle_id = f"{asset}:{minute_key}"

        completed_candle: CandlePayload | None = None

        # Check if we have an existing candle for a *different* minute
        current = self._candles.get(asset)
        if current is not None and current["minute_key"] != minute_key:
            # The previous minute's candle is complete
            completed_candle = CandlePayload(
                timestamp=datetime.fromisoformat(current["minute_key"]).replace(tzinfo=UTC),
                open=current["open"],
                high=current["high"],
                low=current["low"],
                close=current["close"],
                volume=current["volume"],
            )
            # Start a new candle
            self._candles[asset] = {
                "minute_key": minute_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
        elif current is None:
            # First ticker for this asset
            self._candles[asset] = {
                "minute_key": minute_key,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
            }
        else:
            # Same minute — update OHLCV
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["volume"] += volume

        return completed_candle

    def flush_all(self) -> list[tuple[str, CandlePayload]]:
        """Flush all in-progress candles (used on shutdown)."""
        results = []
        for asset, current in self._candles.items():
            candle = CandlePayload(
                timestamp=datetime.fromisoformat(current["minute_key"]).replace(tzinfo=UTC),
                open=current["open"],
                high=current["high"],
                low=current["low"],
                close=current["close"],
                volume=current["volume"],
            )
            results.append((asset, candle))
        self._candles.clear()
        return results


def is_enabled() -> bool:
    return os.getenv("ENABLE_UPBIT_COLLECTOR", "false").lower() == "true"


def _build_subscription(assets: list[str]) -> str:
    """Build the JSON subscription message for Upbit WebSocket."""
    return json.dumps([
        {"ticket": str(uuid.uuid4())},
        {"type": "ticker", "codes": assets},
        {"format": "DEFAULT"},
    ])


def _parse_message(raw: bytes | str) -> dict | None:
    """Parse an Upbit WebSocket message, handling both gzip and JSON."""
    try:
        if isinstance(raw, bytes):
            # Try gzip decompression first
            try:
                decompressed = gzip.decompress(raw)
                return json.loads(decompressed)
            except (gzip.BadGzipFile, OSError):
                # Not gzip, try plain JSON
                return json.loads(raw)
        else:
            return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


async def _post_candle(asset: str, candle: CandlePayload) -> None:
    """POST a completed candle to the local market-data ingestion API."""
    import httpx

    url = f"{LOCAL_INGEST_BASE}/candles/{asset}"
    payload = candle.model_dump(mode="json")
    payload["timestamp"] = candle.timestamp.isoformat()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code < 300:
                logger.info("Ingested Upbit candle %s at %s", asset, candle.timestamp.isoformat())
                candle_ingest_total.labels(asset=asset, status="success").inc()
                # Redundant market.candle.ingested publish removed — see
                # binance_collector for rationale. The HTTP ingest route
                # owns the canonical market.candle.updated.{asset} publish.
            else:
                logger.warning("Upbit ingest rejected %s (status=%d)", asset, response.status_code)
                candle_ingest_total.labels(asset=asset, status="failed").inc()
    except Exception:
        logger.exception("Failed to POST Upbit candle for %s", asset)
        candle_ingest_total.labels(asset=asset, status="failed").inc()


async def _run_ws_loop() -> None:
    """Upbit WebSocket loop with exponential-backoff reconnection."""
    assets = [a.strip() for a in MONITORED_ASSETS.split(",") if a.strip()]
    if not assets:
        logger.warning("No assets configured for Upbit collector")
        return

    aggregator = _CandleAggregator()
    delay = RECONNECT_DELAY_SECONDS

    while True:
        try:
            logger.info("Connecting to Upbit WebSocket for %d assets: %s", len(assets), assets)
            async with websockets.connect(UPBIT_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                delay = RECONNECT_DELAY_SECONDS
                # Send subscription message
                sub_msg = _build_subscription(assets)
                await ws.send(sub_msg)
                logger.info("Connected to Upbit WebSocket, subscription sent")

                async for raw_message in ws:
                    data = _parse_message(raw_message)
                    if data is None:
                        continue

                    # Upbit ticker messages have "type": "ticker"
                    if data.get("type") != "ticker":
                        continue

                    asset = data.get("code", "")
                    trade_price = data.get("trade_price")
                    trade_volume = data.get("trade_volume", 0.0)
                    trade_timestamp_ms = data.get("trade_timestamp")

                    if not asset or trade_price is None:
                        continue

                    try:
                        if trade_timestamp_ms is not None:
                            trade_time = datetime.fromtimestamp(
                                int(trade_timestamp_ms) / 1000, tz=UTC
                            )
                        else:
                            trade_time = datetime.now(UTC)
                    except (ValueError, TypeError):
                        trade_time = datetime.now(UTC)

                    completed = aggregator.update(
                        asset=asset,
                        price=float(trade_price),
                        volume=float(trade_volume),
                        trade_time=trade_time,
                    )

                    if completed is not None:
                        logger.info("Upbit 1m candle closed: %s at %s", asset, completed.timestamp.isoformat())
                        try:
                            await _post_candle(asset, completed)
                        except Exception:
                            logger.exception("Error posting Upbit candle for %s", asset)

        except asyncio.CancelledError:
            logger.info("Upbit collector task cancelled, shutting down")
            # Flush remaining candles
            for asset, candle in aggregator.flush_all():
                try:
                    await _post_candle(asset, candle)
                except Exception:
                    logger.exception("Error flushing Upbit candle for %s on shutdown", asset)
            return
        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.InvalidURI,
            OSError,
        ) as exc:
            logger.warning(
                "Upbit WebSocket disconnected (%s). Reconnecting in %ds...",
                exc,
                delay,
            )
        except Exception:
            logger.exception(
                "Unexpected error in Upbit collector. Reconnecting in %ds...",
                delay,
            )

        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY_SECONDS)


_task: asyncio.Task[None] | None = None


async def start() -> None:
    """Launch the collector as a background asyncio task."""
    global _task, _event_bus
    if _task is not None:
        logger.warning("Upbit collector already running")
        return
    # See binance_collector: HTTP ingest route owns the NATS publish.
    _event_bus = None
    logger.info("Starting Upbit WebSocket collector background task")
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
    logger.info("Upbit collector stopped")
