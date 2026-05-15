"""Example alpha: 20/50 SMA crossover.

This is intentionally simple — it exists to (a) prove the plugin pattern,
(b) give a copy-paste starting point for a new alpha, and (c) let the
public quant-platform boot with at least one alpha registered.

Wire-up:
  export QUANT_ALPHA_PLUGINS=examples.sma_crossover_alpha
"""
from __future__ import annotations

import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig
from shared.alpha.registry import register_alpha


class SMACrossoverAlpha(Alpha):
    """Classic 20/50 SMA crossover.

    Long when SMA_fast > SMA_slow, short otherwise. No volatility scaling,
    no overlay — keeps the example small. Position is clipped to [-1, 1]
    by the base class.
    """

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        fast = int(self.config.params.get("fast", 20))
        slow = int(self.config.params.get("slow", 50))
        sma_fast = df["close"].rolling(fast, min_periods=fast).mean()
        sma_slow = df["close"].rolling(slow, min_periods=slow).mean()
        position = (sma_fast > sma_slow).astype(float) * 2 - 1
        return position.fillna(0.0)


register_alpha("sma_crossover", lambda cfg=None: SMACrossoverAlpha(cfg or AlphaConfig(name="sma_crossover")))
