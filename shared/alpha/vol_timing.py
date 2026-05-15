"""Volatility timing meta-alpha.

Not a directional alpha — instead, produces a SCALING signal in [0, 1]
that modulates how much the ensemble should trade. When realized vol is
in the top quartile of its 1-year history, the engine has historically
performed well → scale up. When in the bottom quartile → scale down.

This addresses the per-year breakdown finding: 2020 (high vol) = Sharpe 2.3,
2023 (low vol) = Sharpe -1.9. The engine's edge IS the volatility.

Since this is a scaling alpha (not directional), it outputs +1 for "trade
aggressively" and a small fraction for "trade conservatively". It should
be combined multiplicatively with the ensemble, not additively.

Implementation:
  1) Compute 24h realized vol (annualized)
  2) Z-score against 1-year rolling distribution
  3) Map z to scale: z > 0.5 → 1.0, z < -0.5 → 0.3, linear between
  4) This is a SLOW signal (changes over days/weeks) → very low turnover

For ensemble integration: multiply ensemble target_position by this scale.
We expose it as a standard Alpha that returns the position-scale-factor
rather than a directional signal. The ensemble treats it as an alpha whose
"position" is always positive — it modulates via the alpha gate mechanism.

Alternative integration (cleaner): add as a post-processing step in the
ensemble allocator. But for now, standalone testing is more important.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal


class VolTimingAlpha(Alpha):
    """Outputs a position-scale factor [floor, 1.0] based on vol regime.

    NOT a directional alpha. Multiply with the ensemble's target position.
    Standalone evaluation will show near-zero Sharpe (it doesn't pick direction);
    its value is in FILTERING when the ensemble should be active.
    """
    def __init__(self, config: AlphaConfig | None = None) -> None:
        super().__init__(config or AlphaConfig(name="vol_timing"))

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        vol_window = int(p.get("vol_window", 24))
        z_window = int(p.get("z_window", 24 * 365))
        z_min_periods = int(p.get("z_min_periods", 24 * 90))
        floor = float(p.get("floor", 0.3))
        z_low = float(p.get("z_low", -0.5))
        z_high = float(p.get("z_high", 0.5))

        close = df["close"].astype(float)
        log_ret = np.log(close / close.shift(1)).fillna(0.0)

        # Realized vol (annualized)
        rv = log_ret.rolling(vol_window, min_periods=vol_window).std(ddof=0) * np.sqrt(24 * 365)

        # Z-score against 1-year history
        mean = rv.rolling(z_window, min_periods=z_min_periods).mean()
        std = rv.rolling(z_window, min_periods=z_min_periods).std(ddof=0).replace(0, np.nan)
        z = ((rv - mean) / std).fillna(0.0)

        # Map: z_low → floor, z_high → 1.0, linear between
        denom = max(z_high - z_low, 1e-6)
        scale = floor + (z - z_low) * (1.0 - floor) / denom
        scale = scale.clip(floor, 1.0)

        # Before warmup (no z available), return 1.0 (neutral)
        warm = mean.notna()
        scale = scale.where(warm, 1.0)

        return scale
