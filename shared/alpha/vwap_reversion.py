"""VWAP reversion alpha.

Price tends to mean-revert to the Volume-Weighted Average Price over
medium-horizon windows (8-24h). Unlike Bollinger-based mean reversion
(which uses price-only), VWAP incorporates volume → heavier bars pull
the anchor more → the reversion target is more robust.

Signal construction:
  1) Compute rolling VWAP over `vwap_window` hours (default 12h)
  2) Z-score of (close − VWAP) / rolling_std
  3) Fade the deviation: z > 1.5 → short, z < -1.5 → long
  4) Structural-trend gate: only allow mean-reversion trades if the macro
     trend is flat (EMA50 ≈ EMA200). This avoids buying the dip in a
     bear market — the v3.2 mean_reversion killer.
  5) Apply vol_target_scale

Reference: Bouchaud et al. (2018) "Trades, Quotes and Prices" ch.11
on VWAP anchoring effects.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal, ema, vol_target_scale


class VWAPReversionAlpha(Alpha):
    def __init__(self, config: AlphaConfig | None = None) -> None:
        super().__init__(config or AlphaConfig(name="vwap_reversion"))

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        vwap_win = int(p.get("vwap_window", 12))
        z_threshold = float(p.get("z_threshold", 1.5))
        smooth = int(p.get("smooth", 4))
        scale_factor = float(p.get("scale", 1.0))
        trend_gate = bool(p.get("trend_gate", True))

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float).replace(0, np.nan)

        # Typical price (OHLC average) — more robust than close-only
        tp = (high + low + close) / 3.0

        # Rolling VWAP = sum(tp * vol) / sum(vol) over the window
        tp_vol = tp * volume
        cum_tp_vol = tp_vol.rolling(vwap_win, min_periods=vwap_win // 2).sum()
        cum_vol = volume.rolling(vwap_win, min_periods=vwap_win // 2).sum()
        vwap = (cum_tp_vol / cum_vol.replace(0, np.nan)).fillna(close)

        # Deviation from VWAP
        dev = close - vwap
        dev_std = dev.rolling(vwap_win * 2, min_periods=vwap_win).std(ddof=0).replace(0, np.nan)
        z = (dev / dev_std).fillna(0.0)

        # Fade: positive z → short (price above VWAP → expect pullback)
        signal = -z
        # Zero-out small z (noise filter)
        signal = signal.where(z.abs() > z_threshold, 0.0)

        # Smooth
        signal = ema(signal, smooth) * scale_factor

        # Tanh
        raw = np.tanh(signal)

        # Structural-trend gate: suppress MR in trending markets
        if trend_gate:
            ema50 = ema(close, 50)
            ema200 = ema(close, 200)
            # Trend strength: |EMA50 - EMA200| / EMA200
            trend_strength = ((ema50 - ema200) / ema200.replace(0, np.nan)).abs().fillna(0.0)
            # When trend_strength > 0.05 (~5%), suppress mean reversion
            gate = (1.0 - (trend_strength / 0.05).clip(0, 1)).fillna(0.0)
            raw = raw * gate

        # Vol-target scale
        vts = vol_target_scale(close, target_vol_annual=0.40)
        raw = raw * vts

        return raw
