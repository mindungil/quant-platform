"""Open-interest / price divergence alpha.

Core insight: when open interest rises while price falls, new shorts
are entering aggressively → short squeeze setup (fade the move). Price
up + OI down = longs taking profit → momentum is hollow (fade). Same
direction = trend confirmed (follow).

Decision grid:
    price_ret    oi_ret     signal
      +            +         +1   (long, trend confirmed)
      +            -          0   (unreliable long)
      -            +         +1   (contrarian long on short pile-up)
      -            -         -1   (trend-following short, real selling)

Smoothed via z-scored rolling window so one-bar spikes don't flip the
position wildly.

Params:
  lookback:    int = 24      # bars for returns calc
  z_window:    int = 168     # z-score window
  oi_csv_path: str | None    # CSV path; loaded lazily on first bar
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig

logger = logging.getLogger(__name__)


_DEFAULT_OI_DIR = Path(__file__).resolve().parents[2] / "data" / "derivatives"


class OiDivergenceAlpha(Alpha):
    DEFAULT_PARAMS = {
        "lookback": 24,
        "z_window": 168,
        "oi_csv_path": None,  # fallback to {DATA_DIR}/{ASSET}_oi_1h.csv
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        config = config or AlphaConfig(name="oi_divergence", asset_type="crypto")
        super().__init__(config)
        user = getattr(config, "params", None) or {}
        self.params = {**self.DEFAULT_PARAMS, **user}
        self._oi_cache: pd.Series | None = None
        self._oi_loaded_for: str | None = None

    def _load_oi(self, asset_hint: str) -> pd.Series | None:
        """Load sumOpenInterest series from CSV. Returns None if missing.

        The incubator validator doesn't know the asset symbol at call
        time, so we take a hint (derived from the config or test harness).
        """
        if self._oi_cache is not None and self._oi_loaded_for == asset_hint:
            return self._oi_cache
        path = self.params.get("oi_csv_path")
        if not path:
            path = os.environ.get("OI_CSV_PATH") or str(
                _DEFAULT_OI_DIR / f"{asset_hint}_oi_1h.csv"
            )
        if not Path(path).exists():
            logger.info("oi_data_unavailable", extra={"asset": asset_hint, "path": path})
            self._oi_cache = pd.Series(dtype=float)
            self._oi_loaded_for = asset_hint
            return self._oi_cache
        try:
            df = pd.read_csv(path)
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            col = "sumOpenInterest" if "sumOpenInterest" in df.columns else df.columns[1]
            self._oi_cache = df[col].astype(float)
            self._oi_loaded_for = asset_hint
        except Exception as exc:
            logger.warning("oi_load_failed", extra={"error": str(exc)[:100]})
            self._oi_cache = pd.Series(dtype=float)
            self._oi_loaded_for = asset_hint
        return self._oi_cache

    def _generate(self, df):
        if isinstance(df, dict):
            raise TypeError("oi_divergence expects a single-asset OHLCV DataFrame")

        # Asset hint: config.name has the alpha name, not asset. Use the
        # environment or the DataFrame's 'asset' column if present, else
        # fall back to BTCUSDT (the only OI series we ship).
        asset_hint = (
            df["asset"].iloc[0] if "asset" in df.columns else os.environ.get("OI_ASSET", "BTCUSDT")
        )
        oi = self._load_oi(asset_hint)

        lookback = int(self.params["lookback"])
        z_win = int(self.params["z_window"])

        close = df["close"].astype(float)
        price_ret = close.pct_change(lookback).fillna(0.0)
        price_z = (
            (price_ret - price_ret.rolling(z_win).mean())
            / price_ret.rolling(z_win).std(ddof=0).replace(0, np.nan)
        ).fillna(0.0)

        if oi.empty:
            # No OI data in this window — emit zero conviction so the
            # alpha is a safe no-op until data is backfilled. This keeps
            # the ensemble stable when the derivatives feed is offline.
            return pd.Series(0.0, index=df.index)

        oi_aligned = oi.reindex(df.index).ffill()
        oi_ret = oi_aligned.pct_change(lookback).fillna(0.0)
        oi_z = (
            (oi_ret - oi_ret.rolling(z_win).mean())
            / oi_ret.rolling(z_win).std(ddof=0).replace(0, np.nan)
        ).fillna(0.0)

        # Trend-confirm when signs agree, contrarian when price down + OI up.
        confirm = ((price_z > 0) & (oi_z > 0)).astype(float)
        trend_short = ((price_z < 0) & (oi_z < 0)).astype(float) * -1.0
        squeeze_long = ((price_z < 0) & (oi_z > 0)).astype(float)
        # price up + OI down = hollow rally, flat. Implicit (no branch).

        raw = confirm + trend_short + squeeze_long
        # Scale by |price_z| so we push harder on stronger divergences.
        position = raw * np.tanh(price_z.abs())
        return position.clip(-1.0, 1.0)
