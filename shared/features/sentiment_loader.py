"""Sentiment data loader for feature engine integration.

Loads composite_score from sentiment_hourly table and aligns to OHLCV index.
Used by MLDiscoveryAlpha and any other alpha that needs sentiment features.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

logger = logging.getLogger("sentiment-loader")

_MARKET_DB_URL = os.getenv(
    "POSTGRES_URL_MARKET",
    os.getenv("TIMESCALE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/market"),
)

# Cache to avoid repeated DB queries within the same process
_cache: dict[str, pd.Series] = {}


def load_sentiment_series(asset: str, use_cache: bool = True) -> pd.Series | None:
    """Load hourly composite_score for an asset from sentiment_hourly.

    Args:
        asset: e.g. "BTC", "BTCUSDT" (USDT suffix is stripped)
        use_cache: reuse cached data within the same process

    Returns:
        pd.Series with DatetimeIndex, or None if insufficient data (<100 rows).
    """
    asset_clean = asset.replace("USDT", "").replace("USD", "")

    if use_cache and asset_clean in _cache:
        return _cache[asset_clean]

    try:
        from shared.persistence import SqlStore
        store = SqlStore(_MARKET_DB_URL)
        rows = store.fetch_all(
            """
            SELECT timestamp, composite_score
            FROM sentiment_hourly
            WHERE asset = :asset AND composite_score IS NOT NULL
            ORDER BY timestamp ASC
            """,
            {"asset": asset_clean},
        )
        if not rows or len(rows) < 100:
            logger.debug("sentiment data insufficient for %s (%d rows)", asset_clean, len(rows) if rows else 0)
            return None

        ts = pd.Series(
            [float(r["composite_score"]) for r in rows],
            index=pd.DatetimeIndex([r["timestamp"] for r in rows]),
            name="sentiment",
        )
        # Remove timezone for compatibility with tz-naive OHLCV indices
        if ts.index.tz is not None:
            ts.index = ts.index.tz_localize(None)

        _cache[asset_clean] = ts
        logger.info("loaded %d sentiment rows for %s", len(ts), asset_clean)
        return ts
    except Exception as e:
        logger.debug("sentiment load failed for %s: %s", asset_clean, str(e)[:100])
        return None


def align_sentiment(sentiment: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Align sentiment series to a target OHLCV index using forward-fill."""
    if sentiment is None:
        return None
    # Handle tz mismatch: strip tz from target if sentiment is tz-naive, or vice versa
    s = sentiment.copy()
    idx = target_index
    if s.index.tz is not None and idx.tz is None:
        s.index = s.index.tz_localize(None)
    elif s.index.tz is None and idx.tz is not None:
        s.index = s.index.tz_localize(idx.tz)
    aligned = s.reindex(idx, method="ffill")
    if aligned.notna().sum() < 100:
        return None
    return aligned
