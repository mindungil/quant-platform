"""Fear & Greed Index contrarian alpha.

Classic crypto contrarian strategy: buy when others are fearful,
sell when others are greedy. Uses the alternative.me Fear & Greed Index.

Key properties:
- External data source (not derived from price → structurally uncorrelated)
- Very low turnover (~10-30 trades/year)
- Daily signal (no intraday noise)
- Contrarian: profits from mean-reversion in sentiment extremes

Position logic:
  FNG < fear_threshold  → long  (scaled by how extreme)
  FNG > greed_threshold → short (scaled by how extreme)
  Between thresholds    → flat or reduced
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal


_DEFAULT_PARAMS = {
    "fear_threshold": 25,      # below this → long
    "greed_threshold": 75,     # above this → short
    "max_position": 0.5,       # max position at extreme
    "smooth_window": 3,        # days of smoothing (avoid single-day spikes)
    "zscore_window": 90,       # days for z-score normalization
}


class FearGreedAlpha(Alpha):
    """Contrarian alpha based on Fear & Greed Index."""

    def __init__(
        self,
        config: AlphaConfig | None = None,
        fng_data: pd.Series | None = None,
    ) -> None:
        if config is None:
            config = AlphaConfig(name="fear_greed", params=dict(_DEFAULT_PARAMS))
        merged = dict(_DEFAULT_PARAMS)
        merged.update(config.params)
        config.params = merged
        super().__init__(config)
        self.fng_data = fng_data

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        if self.fng_data is None:
            return pd.Series(0.0, index=df.index)

        # Resample daily FNG to match df index (forward-fill)
        fng = self.fng_data.reindex(df.index, method="ffill").fillna(50.0)

        # Smooth to avoid single-day spikes
        smooth_bars = p["smooth_window"] * 24  # days → hours
        fng_smooth = fng.rolling(smooth_bars, min_periods=1).mean()

        fear_th = p["fear_threshold"]
        greed_th = p["greed_threshold"]
        max_pos = p["max_position"]

        # Contrarian signal:
        # Fear zone: position scales from 0 at threshold to +max_pos at 0
        # Greed zone: position scales from 0 at threshold to -max_pos at 100
        # Neutral zone: position = 0
        position = pd.Series(0.0, index=df.index)

        # Fear → long (contrarian)
        fear_mask = fng_smooth < fear_th
        fear_intensity = (fear_th - fng_smooth[fear_mask]) / fear_th  # 0 at threshold, 1 at 0
        position[fear_mask] = fear_intensity * max_pos

        # Greed → short (contrarian)
        greed_mask = fng_smooth > greed_th
        greed_intensity = (fng_smooth[greed_mask] - greed_th) / (100 - greed_th)
        position[greed_mask] = -greed_intensity * max_pos

        # Z-score overlay: amplify position when FNG is at multi-month extremes
        zscore_bars = p["zscore_window"] * 24
        fng_z = (fng_smooth - fng_smooth.rolling(zscore_bars, min_periods=zscore_bars // 2).mean()) / \
                fng_smooth.rolling(zscore_bars, min_periods=zscore_bars // 2).std().replace(0, np.nan).fillna(1)

        # Amplify by z-score magnitude (but cap)
        amplifier = (1.0 + fng_z.abs().clip(upper=2) * 0.3)
        position = position * amplifier

        return position.clip(-1, 1).fillna(0.0)
