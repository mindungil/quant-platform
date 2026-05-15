"""Funding-rate carry alpha (perpetual futures).

If the perpetual funding rate is consistently positive, longs are paying
shorts → take the short side and collect funding (and vice versa).

Inputs: OHLCV dataframe must have a `funding_rate` column. If absent (e.g.
for backtesting on plain OHLCV), the alpha emits zero so it never lies
about producing alpha from data it doesn't have.

Position is sized by the magnitude and stability of recent funding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig


class CarryAlpha(Alpha):
    DEFAULT_PARAMS = {
        "lookback": 24,                # bars over which to average funding
        "min_funding_bps": 1.0,        # below this magnitude, ignore (1bp = 0.0001)
        "max_position_funding_bps": 5.0,  # at this magnitude, full size
        "funding_col": "funding_rate",
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="carry", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        col = p["funding_col"]
        if col not in df.columns:
            # Honest no-op: this alpha needs derivatives data
            return pd.Series(0.0, index=df.index)

        funding = df[col].astype(float)
        avg = funding.rolling(p["lookback"], min_periods=p["lookback"]).mean()

        avg_bps = avg * 10000.0
        min_bps = p["min_funding_bps"]
        max_bps = p["max_position_funding_bps"]

        # Sign of funding decides direction (short if positive, long if negative)
        magnitude = ((avg_bps.abs() - min_bps) / max(max_bps - min_bps, 1e-9)).clip(0.0, 1.0)
        direction = -np.sign(avg_bps)  # short positive funding = collect funding
        position = direction * magnitude

        return position.fillna(0.0)
