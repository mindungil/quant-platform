"""Thin HTTP client over market-data /candles/{asset}/history.

The meta-ensemble engine needs a rolling OHLCV window (500+ bars) to
compute alpha positions. We pull it from market-data rather than
re-aggregating candles inside signal-service so the feature truth
stays single-sourced.
"""
from __future__ import annotations

import httpx
import pandas as pd


class MarketDataClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get_history(self, asset: str, limit: int = 500, interval: str = "1h") -> pd.DataFrame:
        """Return an OHLCV DataFrame indexed by timestamp, sorted ascending.

        Empty DataFrame when market-data has no candles for *asset*.
        Caller is expected to handle the too-few-bars case (alphas have
        warmup windows).
        """
        resp = httpx.get(
            f"{self._base_url}/candles/{asset}/history",
            params={"limit": limit, "interval": interval},
            timeout=self._timeout,
        )
        if resp.status_code == 404:
            return pd.DataFrame()
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()
        keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        return df[keep].astype(float)
