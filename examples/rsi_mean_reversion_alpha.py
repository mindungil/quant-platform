"""Example alpha: RSI mean reversion (textbook).

Long when RSI < 30 (oversold), short when RSI > 70 (overbought), flat
elsewhere. Pure pedagogy — for real edge see how the meta-ensemble
combines signals rather than expecting this to make money standalone.

Wire-up:
  export QUANT_ALPHA_PLUGINS=examples.sma_crossover_alpha,examples.rsi_mean_reversion_alpha
"""
from __future__ import annotations

import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, rsi
from shared.alpha.registry import register_alpha


class RsiMeanReversionAlpha(Alpha):
    def _generate(self, df: pd.DataFrame) -> pd.Series:
        period = int(self.config.params.get("period", 14))
        oversold = float(self.config.params.get("oversold", 30))
        overbought = float(self.config.params.get("overbought", 70))
        r = rsi(df["close"], period=period)
        position = pd.Series(0.0, index=df.index)
        position[r < oversold] = 1.0
        position[r > overbought] = -1.0
        return position


register_alpha(
    "rsi_mean_reversion",
    lambda cfg=None: RsiMeanReversionAlpha(cfg or AlphaConfig(name="rsi_mean_reversion")),
)
