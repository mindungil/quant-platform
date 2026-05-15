"""Cross-asset lead-lag alpha.

BTC leads altcoins by 1-4 hours on average. When BTC makes a significant
move, altcoins follow with a delay. This alpha explicitly trades that
timing gap.

Signal construction:
  1) Require an `exog` DataFrame (BTC OHLCV) passed at init
  2) Compute BTC log-return over the last K hours (default 3h)
  3) Apply an asymmetric threshold: BTC move must exceed 0.5 ATR to be
     signal-worthy (filters noise)
  4) Smooth the lagged signal with EMA
  5) If symbol is BTC itself, this alpha is always flat (no self-lead)

Reference: Makarov & Schoar (2020) "Trading and Arbitrage in Cryptocurrency
Markets" documents persistent lead-lag in crypto.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal, ema, atr, vol_target_scale


class LeadLagAlpha(Alpha):
    def __init__(self, config: AlphaConfig | None = None, exog: pd.DataFrame | None = None) -> None:
        super().__init__(config or AlphaConfig(name="lead_lag"))
        self.exog = exog

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        lag_hours = int(p.get("lag_hours", 3))
        atr_period = int(p.get("atr_period", 24))
        threshold_atr = float(p.get("threshold_atr", 0.5))
        smooth = int(p.get("smooth", 4))
        scale_factor = float(p.get("scale", 1.5))

        close = df["close"].astype(float)

        # If no exog (this IS BTC, or not provided), go flat
        if self.exog is None or "close" not in self.exog.columns:
            return pd.Series(0.0, index=close.index)

        # Align BTC data to target's index
        btc_close = self.exog["close"].astype(float).reindex(close.index).ffill()
        btc_high = self.exog["high"].astype(float).reindex(close.index).ffill() if "high" in self.exog.columns else btc_close
        btc_low = self.exog["low"].astype(float).reindex(close.index).ffill() if "low" in self.exog.columns else btc_close

        # BTC return over the lag window
        btc_log_ret = np.log(btc_close / btc_close.shift(lag_hours)).fillna(0.0)

        # Threshold: only act on significant BTC moves (> threshold × ATR)
        btc_atr = atr(btc_high, btc_low, btc_close, period=atr_period)
        btc_atr_pct = (btc_atr / btc_close.replace(0, np.nan)).fillna(0.01)
        threshold = threshold_atr * btc_atr_pct

        # Signal: signed BTC move, zeroed if under threshold
        signal = pd.Series(0.0, index=close.index)
        signal = signal.where(btc_log_ret.abs() <= threshold, btc_log_ret)

        # Smooth and scale
        signal = ema(signal, smooth) * scale_factor

        # Tanh compression
        raw = np.tanh(signal / btc_atr_pct.replace(0, 0.01))

        # Vol-target scale on the target asset (not BTC)
        vts = vol_target_scale(close, target_vol_annual=0.40)
        raw = raw * vts

        return raw
