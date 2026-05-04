"""In-memory cache for FeatureEngine output.

Feature generation is ~1.5s for 5000 bars of 100+ features. Callers
(ML alphas, monitoring endpoint) often request features for the same
window repeatedly within seconds. Caching keyed by (symbol, bar_count,
last_timestamp) cuts cold-start cost to near-zero.

Uses a simple LRU with TTL.
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger("feature-cache")


class FeatureCache:
    """LRU cache for FeatureMatrix objects keyed by dataframe fingerprint."""

    def __init__(self, max_entries: int = 32, ttl_seconds: float = 60.0):
        self._max = max_entries
        self._ttl = ttl_seconds
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _fingerprint(self, df: pd.DataFrame, extra: str = "") -> str:
        """Fingerprint the (symbol, row-count, last-timestamp, last-close) tuple.

        This matches *exactly* when the same asset is requested at the
        same bar boundary, which is the common case. Not intended for
        partial/incremental updates.
        """
        if df.empty:
            return "empty"
        try:
            key = (
                str(df.index[-1]),
                len(df),
                float(df["close"].iloc[-1]) if "close" in df.columns else 0.0,
                extra,
            )
        except Exception:
            return "unfingerprintable"
        return hashlib.sha256(str(key).encode()).hexdigest()[:16]

    def get_or_compute(
        self,
        df: pd.DataFrame,
        compute_fn: Callable[[], Any],
        extra: str = "",
    ) -> Any:
        """Return cached result or compute + cache it."""
        now = time.monotonic()
        key = self._fingerprint(df, extra)

        entry = self._store.get(key)
        if entry is not None:
            stored_at, value = entry
            if now - stored_at < self._ttl:
                self._store.move_to_end(key)
                self._hits += 1
                return value
            # Expired
            del self._store[key]

        self._misses += 1
        value = compute_fn()
        self._store[key] = (now, value)
        self._store.move_to_end(key)
        if len(self._store) > self._max:
            self._store.popitem(last=False)
        return value

    def clear(self) -> None:
        self._store.clear()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "entries": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1), 3),
        }


# Module-level singleton
_global_cache: FeatureCache | None = None


def get_feature_cache() -> FeatureCache:
    global _global_cache
    if _global_cache is None:
        _global_cache = FeatureCache()
    return _global_cache
