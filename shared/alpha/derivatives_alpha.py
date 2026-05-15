"""Derivatives positioning alpha — OI, Long/Short ratio, Taker flow.

Uses genuinely non-price data from Binance Futures derivatives endpoints.
Three sub-signals combined:

1. OI Divergence (contrarian): OI surges while price doesn't → crowding → fade
2. Long/Short Ratio (contrarian): Extreme crowd positioning → go opposite
3. Taker Flow (momentum): Aggressive buying/selling → follow the flow

These signals are structurally uncorrelated with price-based trend alphas
because they measure POSITIONING, not price movement.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal


_DEFAULT_PARAMS = {
    "oi_weight": 0.4,
    "lsr_weight": 0.3,
    "taker_weight": 0.3,
    "oi_div_window": 72,
    "lsr_window": 48,
    "taker_window": 24,
    "signal_clip": 2.0,     # z-score clip before tanh
    "smooth_window": 6,     # smooth final signal (bars)
}


class DerivativesAlpha(Alpha):
    """Positioning-based alpha from derivatives data."""

    def __init__(
        self,
        config: AlphaConfig | None = None,
        derivatives_data: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        if config is None:
            config = AlphaConfig(name="derivatives_alpha", params=dict(_DEFAULT_PARAMS))
        merged = dict(_DEFAULT_PARAMS)
        merged.update(config.params)
        config.params = merged
        super().__init__(config)
        self.derivatives_data = derivatives_data or {}

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        n = len(df)
        close = df["close"].astype(float)
        signals = []
        weights = []

        # 1. OI Divergence (contrarian)
        if "open_interest" in self.derivatives_data:
            oi_df = self.derivatives_data["open_interest"]
            oi = oi_df.reindex(df.index).ffill()
            if "sumOpenInterest" in oi.columns:
                oi_val = oi["sumOpenInterest"].astype(float)
                w = p["oi_div_window"]
                oi_z = _rolling_zscore(oi_val, w)
                price_z = _rolling_zscore(close, w)
                # Divergence: OI rising + price falling = crowded longs about to liquidate
                divergence = oi_z - price_z
                signal_oi = -np.tanh(divergence.clip(-p["signal_clip"], p["signal_clip"]))
                signals.append(signal_oi)
                weights.append(p["oi_weight"])

        # 2. Long/Short Ratio (contrarian)
        if "global_lsr" in self.derivatives_data:
            lsr_df = self.derivatives_data["global_lsr"]
            lsr = lsr_df.reindex(df.index).ffill()
            if "longShortRatio" in lsr.columns:
                lsr_val = lsr["longShortRatio"].astype(float)
                w = p["lsr_window"]
                # Normalize: ratio > 1 = crowded long, < 1 = crowded short
                lsr_z = _rolling_zscore(lsr_val, w)
                # Contrarian: go opposite to the crowd
                signal_lsr = -np.tanh(lsr_z.clip(-p["signal_clip"], p["signal_clip"]))
                signals.append(signal_lsr)
                weights.append(p["lsr_weight"])

        # 3. Taker Flow (momentum)
        if "taker" in self.derivatives_data:
            tk_df = self.derivatives_data["taker"]
            tk = tk_df.reindex(df.index).ffill()
            if "buySellRatio" in tk.columns:
                bsr = tk["buySellRatio"].astype(float)
                w = p["taker_window"]
                bsr_z = _rolling_zscore(bsr, w)
                # Momentum: follow aggressive buyers/sellers
                signal_taker = np.tanh(bsr_z.clip(-p["signal_clip"], p["signal_clip"]))
                signals.append(signal_taker)
                weights.append(p["taker_weight"])

        if not signals:
            return pd.Series(0.0, index=df.index)

        # Weighted combination
        total_weight = sum(weights)
        combined = pd.Series(0.0, index=df.index)
        for sig, w in zip(signals, weights):
            combined += (w / total_weight) * sig.fillna(0)

        # Smooth
        sw = p["smooth_window"]
        if sw > 1:
            combined = combined.rolling(sw, min_periods=1).mean()

        return combined.clip(-1, 1).fillna(0)


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window // 2).mean()
    std = series.rolling(window, min_periods=window // 2).std(ddof=0)
    z = (series - mean) / std.replace(0, np.nan)
    return z.fillna(0.0)
