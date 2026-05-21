"""Cross-venue price divergence — data-quality sanity check.

Distinct from the kimchi-premium *factor* (`shared/factors/kimchi_premium.py`),
which trades the spread, this is an *operational* check: if Binance reports
BTC=$60k while Upbit reports a USD-equivalent of $80k, one of the feeds is
likely stale or wrong and signals built from it would be poisoned.

Emits a Prometheus gauge per base asset every CROSS_VENUE_CHECK_SECONDS
seconds. Log WARN when divergence exceeds CROSS_VENUE_WARN_PCT. Alert rule
attachment is deferred to G11 (Phase G-OBS).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone

from prometheus_client import Gauge

from app.db.repository import market_data_repository
from shared.factors.kimchi_premium import fetch_usdkrw

UTC = timezone.utc
logger = logging.getLogger("cross_venue_check")

# (binance_symbol, upbit_symbol, base_label)
ASSET_PAIRS: list[tuple[str, str, str]] = [
    ("BTCUSDT", "KRW-BTC", "BTC"),
    ("ETHUSDT", "KRW-ETH", "ETH"),
    ("SOLUSDT", "KRW-SOL", "SOL"),
]

CHECK_INTERVAL_SECONDS = float(os.getenv("CROSS_VENUE_CHECK_SECONDS", "60"))
DIVERGENCE_WARN_THRESHOLD = float(os.getenv("CROSS_VENUE_WARN_PCT", "0.05"))

cross_venue_divergence = Gauge(
    "cross_venue_price_divergence",
    "USD-equivalent |binance - upbit/fx| / binance, per base asset. NaN if input missing.",
    ["base_asset"],
)
cross_venue_usdkrw_rate = Gauge(
    "cross_venue_usdkrw_rate",
    "USD/KRW reference rate used by the cross-venue check.",
)


def _to_nan() -> float:
    return float("nan")


async def _run_check_loop() -> None:
    logger.info(
        "Cross-venue check started: interval=%ss threshold=%.2f%% pairs=%s",
        CHECK_INTERVAL_SECONDS,
        DIVERGENCE_WARN_THRESHOLD * 100,
        [p[2] for p in ASSET_PAIRS],
    )
    while True:
        try:
            usdkrw = await asyncio.to_thread(fetch_usdkrw)
            if usdkrw and usdkrw > 0:
                cross_venue_usdkrw_rate.set(usdkrw)
            else:
                cross_venue_usdkrw_rate.set(_to_nan())

            for binance_sym, upbit_sym, base in ASSET_PAIRS:
                bin_candle = await asyncio.to_thread(market_data_repository.get_latest, binance_sym)
                upbit_candle = await asyncio.to_thread(market_data_repository.get_latest, upbit_sym)

                if (
                    bin_candle is None
                    or upbit_candle is None
                    or not usdkrw
                    or usdkrw <= 0
                    or bin_candle.close <= 0
                ):
                    cross_venue_divergence.labels(base_asset=base).set(_to_nan())
                    continue

                upbit_usd = upbit_candle.close / usdkrw
                divergence = abs(bin_candle.close - upbit_usd) / bin_candle.close
                cross_venue_divergence.labels(base_asset=base).set(divergence)

                if divergence > DIVERGENCE_WARN_THRESHOLD:
                    logger.warning(
                        "Cross-venue divergence: %s binance=%.2f upbit=%.0f KRW (=%.2f USD @ %.1f) div=%.2f%%",
                        base,
                        bin_candle.close,
                        upbit_candle.close,
                        upbit_usd,
                        usdkrw,
                        divergence * 100,
                    )
        except asyncio.CancelledError:
            logger.info("Cross-venue check cancelled, shutting down")
            return
        except Exception:
            logger.exception("Cross-venue check iteration failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


_task: asyncio.Task[None] | None = None


def is_enabled() -> bool:
    return os.getenv("ENABLE_CROSS_VENUE_CHECK", "true").lower() == "true"


async def start() -> None:
    global _task
    if _task is not None:
        logger.warning("Cross-venue check already running")
        return
    logger.info("Starting cross-venue check background task")
    _task = asyncio.create_task(_run_check_loop())


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    logger.info("Cross-venue check stopped")
