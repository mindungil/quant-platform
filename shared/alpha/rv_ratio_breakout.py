"""Realized-vol ratio breakout.

Signal idea: when short-window realized volatility expands relative to
the long-window baseline, the market is transitioning into a trending
regime — go with the prevailing directional drift. When RV contracts,
stand flat (no conviction either way).

Formally, position ∝ sign(drift) × tanh(RV_short / RV_long - 1),
clipped to [-1, 1]. Filters out chop and sizes into breakouts without
needing explicit regime labels.

References:
  - Andersen, Bollerslev et al. — RV as the cleanest vol proxy
  - Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum"
  - Barroso & Santa-Clara (2015) "Momentum Has Its Moments" (vol scaling)

Params (AlphaConfig.params):
  short_window: int = 24   # 1 day at 1h bars
  long_window:  int = 168  # 1 week at 1h bars
  drift_window: int = 48   # 2-day drift for direction
  activation:   float = 0.30  # |RV_ratio - 1| below this → flat
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig


class RvRatioBreakoutAlpha(Alpha):
    DEFAULT_PARAMS = {
        "short_window": 24,
        "long_window": 168,
        "drift_window": 48,
        "activation": 0.30,
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        config = config or AlphaConfig(name="rv_ratio_breakout", asset_type="crypto")
        super().__init__(config)
        user = getattr(config, "params", None) or {}
        self.params = {**self.DEFAULT_PARAMS, **user}

    def _generate(self, df):
        if isinstance(df, dict):
            raise TypeError("rv_ratio_breakout expects a single-asset OHLCV DataFrame")
        close = df["close"].astype(float)
        log_ret = np.log(close).diff().fillna(0.0)

        short = self.params["short_window"]
        long = self.params["long_window"]
        drift_w = self.params["drift_window"]
        activation = self.params["activation"]

        # RV = sqrt(rolling mean of squared log-returns). Uses squared
        # returns rather than rolling std(ddof=1) so zero-move bars
        # contribute honestly (std drops noise bars).
        rv_short = np.sqrt(log_ret.pow(2).rolling(short, min_periods=short).mean())
        rv_long = np.sqrt(log_ret.pow(2).rolling(long, min_periods=long).mean())
        ratio = (rv_short / rv_long.replace(0.0, np.nan)).fillna(1.0)

        # Directional drift: mean of recent log returns, normalized by
        # rv_long so magnitude is in standard deviations.
        drift = log_ret.rolling(drift_w, min_periods=drift_w).mean()
        drift_sign = np.sign(drift / rv_long.replace(0.0, np.nan)).fillna(0.0)

        # Activation: linear-then-tanh response above *activation*.
        excess = (ratio - 1.0).abs() - activation
        magnitude = np.tanh(np.maximum(excess, 0.0) * 2.0)

        position = drift_sign * magnitude
        # Fade slightly when ratio < 1 (RV contracting) — suggests
        # the current drift is losing conviction, not a fresh breakout.
        contraction_mask = (ratio < 1.0).astype(float)
        position = position * (1.0 - 0.5 * contraction_mask)

        return position.clip(-1.0, 1.0).fillna(0.0)
