"""Long-short ratio contrarian alpha.

Binance publishes the long-short account ratio for top traders every
1h. Extreme crowding in one direction historically precedes a mean
reversion: when top traders are 75% long, price has already priced in
most of the bullish case; a flush is more likely than a continuation.

Signal = -z_score(LSR) with soft activation. Zero-out when the series
is near its mean (no crowding, no edge).

Params:
  z_window:    int = 168
  entry_z:     float = 1.5  # |z| below this → flat
  lsr_csv_path: str | None
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig

logger = logging.getLogger(__name__)

_DEFAULT_LSR_DIR = Path(__file__).resolve().parents[2] / "data" / "derivatives"


class LsrContrarianAlpha(Alpha):
    DEFAULT_PARAMS = {
        "z_window": 168,
        "entry_z": 1.5,
        "lsr_csv_path": None,  # fallback to {DATA_DIR}/{ASSET}_lsr_1h.csv
        # Use "top_1h" (top trader positions) or "lsr_1h" (all accounts).
        # Top traders are more informed, so their LSR is the stronger
        # contrarian signal — but it's also less well-populated.
        "lsr_variant": "top_1h",
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        config = config or AlphaConfig(name="lsr_contrarian", asset_type="crypto")
        super().__init__(config)
        user = getattr(config, "params", None) or {}
        self.params = {**self.DEFAULT_PARAMS, **user}
        self._lsr_cache: pd.Series | None = None
        self._lsr_loaded_for: str | None = None

    def _load_lsr(self, asset_hint: str) -> pd.Series | None:
        if self._lsr_cache is not None and self._lsr_loaded_for == asset_hint:
            return self._lsr_cache
        path = self.params.get("lsr_csv_path")
        if not path:
            variant = self.params.get("lsr_variant", "top_1h")
            path = os.environ.get("LSR_CSV_PATH") or str(
                _DEFAULT_LSR_DIR / f"{asset_hint}_{variant}.csv"
            )
        if not Path(path).exists():
            logger.info("lsr_data_unavailable", extra={"asset": asset_hint, "path": path})
            self._lsr_cache = pd.Series(dtype=float)
            self._lsr_loaded_for = asset_hint
            return self._lsr_cache
        try:
            df = pd.read_csv(path)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            col = "longShortRatio" if "longShortRatio" in df.columns else df.columns[1]
            self._lsr_cache = df[col].astype(float)
            self._lsr_loaded_for = asset_hint
        except Exception as exc:
            logger.warning("lsr_load_failed", extra={"error": str(exc)[:100]})
            self._lsr_cache = pd.Series(dtype=float)
            self._lsr_loaded_for = asset_hint
        return self._lsr_cache

    def _generate(self, df):
        if isinstance(df, dict):
            raise TypeError("lsr_contrarian expects a single-asset OHLCV DataFrame")
        asset_hint = (
            df["asset"].iloc[0] if "asset" in df.columns else os.environ.get("LSR_ASSET", "BTCUSDT")
        )
        lsr = self._load_lsr(asset_hint)
        if lsr.empty:
            return pd.Series(0.0, index=df.index)

        z_win = int(self.params["z_window"])
        entry_z = float(self.params["entry_z"])

        aligned = lsr.reindex(df.index).ffill()
        rolling_mean = aligned.rolling(z_win, min_periods=max(z_win // 4, 24)).mean()
        rolling_std = aligned.rolling(z_win, min_periods=max(z_win // 4, 24)).std(ddof=0)
        z = ((aligned - rolling_mean) / rolling_std.replace(0, np.nan)).fillna(0.0)

        # Contrarian: long when crowded short (z very negative), short
        # when crowded long (z very positive). Zero inside the dead zone.
        over_entry = (z.abs() > entry_z).astype(float)
        position = (-np.sign(z) * over_entry * np.tanh(z.abs() - entry_z)).fillna(0.0)
        return position.clip(-1.0, 1.0)
