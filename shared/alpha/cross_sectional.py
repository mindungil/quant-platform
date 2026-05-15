"""Cross-sectional momentum alpha.

Given a dict of {asset: OHLCV df}, ranks assets by trailing return at each
bar and goes long the top quantile / short the bottom quantile. This is the
classical Jegadeesh-Titman cross-sectional momentum factor adapted to crypto.

Output is a *single Series*: the position on a notional basket "long winners
minus losers". The portfolio ensemble can then size this signal alongside
single-asset alphas. For per-asset position output, use `generate_per_asset`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig


class CrossSectionalMomentumAlpha(Alpha):
    DEFAULT_PARAMS = {
        "lookback": 168,            # ~7 days of hourly bars
        "skip": 24,                 # skip last day to avoid short-term reversal
        "long_quantile": 0.3,       # top 30%
        "short_quantile": 0.3,      # bottom 30%
        "rebalance_every": 24,      # rebalance every N bars
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="cross_sectional_momentum", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df_or_dict) -> pd.Series:
        if not isinstance(df_or_dict, dict):
            raise TypeError("CrossSectionalMomentumAlpha requires dict of {asset: df}")
        per_asset = self.generate_per_asset(df_or_dict)
        if not per_asset:
            return pd.Series(dtype=float)
        # Aggregate to a single basket signal: average of per-asset positions
        frame = pd.DataFrame(per_asset)
        return frame.mean(axis=1).fillna(0.0)

    def generate_per_asset(self, panel: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
        p = self.config.params
        closes = pd.DataFrame({k: v["close"] for k, v in panel.items()}).sort_index()
        # Trailing return with skip
        log_close = np.log(closes)
        ret = (log_close.shift(p["skip"]) - log_close.shift(p["skip"] + p["lookback"]))

        positions = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        rebalance = p["rebalance_every"]
        last_w = pd.Series(0.0, index=closes.columns)

        for i in range(len(closes)):
            if i % rebalance == 0:
                row = ret.iloc[i].dropna()
                if len(row) >= 3:
                    n_long = max(1, int(round(len(row) * p["long_quantile"])))
                    n_short = max(1, int(round(len(row) * p["short_quantile"])))
                    sorted_row = row.sort_values(ascending=False)
                    longs = sorted_row.head(n_long).index
                    shorts = sorted_row.tail(n_short).index
                    last_w[:] = 0.0
                    last_w.loc[longs] = 1.0 / n_long
                    last_w.loc[shorts] = -1.0 / n_short
            positions.iloc[i] = last_w.values

        return {col: positions[col] for col in positions.columns}
