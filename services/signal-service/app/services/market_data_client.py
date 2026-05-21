"""Thin HTTP client over market-data /candles/{asset}/history.

The meta-ensemble engine needs a rolling OHLCV window (500+ bars) to
compute alpha positions. We pull it from market-data rather than
re-aggregating candles inside signal-service so the feature truth
stays single-sourced.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pandas as pd


class MarketDataClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get_latest_timestamp(self, asset: str) -> datetime | None:
        """Return UTC timestamp of the most recent candle, or None if absent.

        G4 staleness gate uses this to detect a dead venue feed even when
        feature-store still serves cached values.
        """
        try:
            resp = httpx.get(
                f"{self._base_url}/candles/{asset}/latest",
                timeout=self._timeout,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            ts = resp.json().get("timestamp")
            if not ts:
                return None
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            return None

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
