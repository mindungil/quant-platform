"""Return skewness reversion alpha.

Structurally different from trend/momentum — uses the SHAPE of the
return distribution (3rd moment), not the direction (1st moment).

When rolling return skewness is extremely negative (crash-like left
tail), markets statistically bounce. When extremely positive (euphoria
spike), they tend to pull back. This is the "panic mean-reversion"
effect documented by Brunnermeier et al. (2008).

Key design choices for cost-resilience:
  - Long rolling window (168h = 7d) → slow signal → low turnover
  - High dead-zone (|z| > 1.5 to trade) → only act on extreme skew
  - EMA smoothing → no whipsaw
  - No structural-trend gate needed (skew is inherently regime-aware:
    it only fires after extreme events)

Expected standalone Sharpe: 0.3-0.8 (mean-reversion-like but orthogonal
to trend-following because it captures tail events, not trend direction).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, ema, vol_target_scale


class SkewReversionAlpha(Alpha):
    def __init__(self, config: AlphaConfig | None = None) -> None:
        super().__init__(config or AlphaConfig(name="skew_reversion"))

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        skew_window = int(p.get("skew_window", 168))       # 7d rolling skewness
        z_window = int(p.get("z_window", 720))              # 30d baseline for z-score
        dead_zone = float(p.get("dead_zone", 1.5))          # only trade extreme skew
        smooth = int(p.get("smooth", 12))                   # 12h EMA smoothing
        scale = float(p.get("scale", 0.8))

        close = df["close"].astype(float)
        log_ret = np.log(close / close.shift(1)).fillna(0.0)

        # Rolling skewness of returns
        skew = log_ret.rolling(skew_window, min_periods=skew_window // 2).skew().fillna(0.0)

        # Z-score skewness against its own longer-run distribution
        mean = skew.rolling(z_window, min_periods=z_window // 3).mean()
        std = skew.rolling(z_window, min_periods=z_window // 3).std(ddof=0).replace(0, np.nan)
        z = ((skew - mean) / std).fillna(0.0)

        # Fade: negative skew z (crash-like) → go long (expect bounce)
        #        positive skew z (euphoria) → go short (expect pullback)
        signal = -z

        # Dead zone: only trade extremes
        signal = signal.where(z.abs() > dead_zone, 0.0)

        # Smooth
        signal = ema(signal, smooth) * scale

        # Tanh + vol scaling
        raw = np.tanh(signal)
        vts = vol_target_scale(close, target_vol_annual=0.40)
        return raw * vts
