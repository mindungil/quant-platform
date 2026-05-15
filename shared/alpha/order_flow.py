"""Order-flow imbalance alpha.

Exploits the information in the taker buy ratio: the fraction of volume
executed by aggressive buyers (taker buy volume / total volume). At hourly
bars, extreme buy or sell imbalance predicts short-term continuation.

Signal construction:
  1) Compute taker_buy_ratio = taker_buy_base / volume (if available)
  2) Z-score of taker_buy_ratio over a rolling window (default 48h)
  3) Smooth with short EMA (default 6h) to reduce whipsaw
  4) Scale by tanh → position in [-1, 1]
  5) Apply vol_target_scale for per-bar sizing stability

This alpha has LOW correlation with trend/momentum because it uses volume
microstructure, not price patterns. Academic reference: Cont, Kukanov &
Stoikov (2014) "The price impact of order book events".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal, ema, vol_target_scale


class OrderFlowAlpha(Alpha):
    def __init__(self, config: AlphaConfig | None = None) -> None:
        super().__init__(config or AlphaConfig(name="order_flow"))

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        z_win = int(p.get("z_window", 48))
        smooth = int(p.get("smooth", 6))
        scale_factor = float(p.get("scale", 1.2))

        close = df["close"].astype(float)
        volume = df["volume"].astype(float)

        # Taker buy ratio — how much of bar volume was aggressive buying
        if "taker_buy_base" in df.columns:
            taker = pd.to_numeric(df["taker_buy_base"], errors="coerce").fillna(0.0)
        else:
            # Fallback: use (close - low) / (high - low) as proxy
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            rng = (high - low).replace(0, np.nan)
            taker = ((close - low) / rng).fillna(0.5) * volume

        # Taker buy ratio: 0 = all selling, 1 = all buying
        tbr = (taker / volume.replace(0, np.nan)).fillna(0.5)

        # Rolling z-score of tbr — detects abnormal buying/selling pressure
        mean = tbr.rolling(z_win, min_periods=z_win // 2).mean()
        std = tbr.rolling(z_win, min_periods=z_win // 2).std(ddof=0).replace(0, np.nan)
        z = ((tbr - mean) / std).fillna(0.0)

        # Smooth to reduce noise
        signal = ema(z, smooth) * scale_factor

        # Position sizing: tanh compression
        raw = np.tanh(signal)

        # Vol-target scale
        vts = vol_target_scale(close, target_vol_annual=0.40)
        raw = raw * vts

        return raw
