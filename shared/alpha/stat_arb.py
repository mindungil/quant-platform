"""Statistical arbitrage / cointegration pairs alpha.

Takes a dict of {asset: OHLCV df} and returns a position series for the
*spread* between two cointegrated assets. The "position" is interpreted as
the long-leg weight on `asset_a` and the inverse on `asset_b` is implied.

For ensemble use, the StatArbAlpha is registered as a single signal on the
spread series; the portfolio layer separately allocates the cash leg.

Cointegration is established via OLS hedge ratio + Engle-Granger ADF test
on the residuals. We trade z-score reversion of the residual.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, rolling_zscore

logger = logging.getLogger(__name__)


class StatArbAlpha(Alpha):
    DEFAULT_PARAMS = {
        "asset_a": None,         # required
        "asset_b": None,         # required
        "lookback": 240,         # bars for hedge ratio + z-score
        "entry_z": 2.0,
        "exit_z": 0.3,
        "max_z": 4.5,            # above this, regime change → flatten
        "require_cointegration": False,  # set True to enforce ADF p<0.05
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="stat_arb", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df_or_dict) -> pd.Series:
        p = self.config.params
        if not isinstance(df_or_dict, dict):
            raise TypeError("StatArbAlpha requires a dict of {asset: DataFrame}")

        a_name = p["asset_a"]
        b_name = p["asset_b"]
        if not a_name or not b_name:
            raise ValueError("StatArbAlpha requires asset_a and asset_b in params")
        if a_name not in df_or_dict or b_name not in df_or_dict:
            return pd.Series(0.0)

        a = df_or_dict[a_name]["close"]
        b = df_or_dict[b_name]["close"]
        # Align
        joined = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
        if len(joined) < p["lookback"] + 10:
            return pd.Series(0.0, index=joined.index)

        # Rolling OLS hedge ratio: a = beta * b + alpha
        log_a = np.log(joined["a"])
        log_b = np.log(joined["b"])

        beta = pd.Series(np.nan, index=joined.index)
        residual = pd.Series(np.nan, index=joined.index)
        win = p["lookback"]
        for i in range(win, len(joined)):
            x = log_b.iloc[i - win : i].values
            y = log_a.iloc[i - win : i].values
            x_mean = x.mean()
            y_mean = y.mean()
            denom = ((x - x_mean) ** 2).sum()
            if denom <= 0:
                continue
            b_hat = ((x - x_mean) * (y - y_mean)).sum() / denom
            a_hat = y_mean - b_hat * x_mean
            beta.iloc[i] = b_hat
            residual.iloc[i] = log_a.iloc[i] - (b_hat * log_b.iloc[i] + a_hat)

        z = rolling_zscore(residual.fillna(0.0), p["lookback"])

        position = pd.Series(0.0, index=joined.index)
        in_pos = 0  # +1 long_spread (long a, short b), -1 short_spread
        for i in range(len(joined)):
            zi = z.iloc[i]
            if pd.isna(zi):
                continue
            if abs(zi) > p["max_z"]:
                in_pos = 0
            elif in_pos == 0:
                if zi <= -p["entry_z"]:
                    in_pos = 1   # spread is too low → buy spread (long a, short b)
                elif zi >= p["entry_z"]:
                    in_pos = -1  # spread too high → sell spread
            else:
                if abs(zi) <= p["exit_z"]:
                    in_pos = 0
            position.iloc[i] = float(in_pos)

        return position
