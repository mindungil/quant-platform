"""Cross-asset alpha — BTC vs traditional finance correlations.

Uses daily EURUSD (DXY proxy) and Gold (PAXG) data to generate
signals based on macro regime shifts:

1. USD strength (EURUSD falling) → risk-off → BTC tends to fall
2. Gold rising → safe-haven bid → mixed for BTC (correlated in stress)
3. BTC-Gold correlation regime → when positive, both are "risk hedges"

These provide a genuinely different information source than crypto-native data.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig


_DEFAULT_PARAMS = {
    "dxy_window": 72,          # hours for DXY momentum
    "gold_window": 72,
    "corr_window": 168,        # BTC-Gold rolling correlation window
    "dxy_weight": 0.4,
    "gold_weight": 0.3,
    "corr_weight": 0.3,
    "smooth": 8,
}


class CrossAssetAlpha(Alpha):
    """Macro cross-asset signal using DXY proxy and Gold."""

    def __init__(
        self,
        config: AlphaConfig | None = None,
        tradfi_data: pd.DataFrame | None = None,
    ) -> None:
        if config is None:
            config = AlphaConfig(name="cross_asset", params=dict(_DEFAULT_PARAMS))
        merged = dict(_DEFAULT_PARAMS)
        merged.update(config.params)
        config.params = merged
        super().__init__(config)
        self.tradfi_data = tradfi_data

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        if self.tradfi_data is None:
            return pd.Series(0.0, index=df.index)

        close = df["close"].astype(float)
        tf = self.tradfi_data.reindex(df.index).ffill()

        signals = []
        weights = []

        # 1. DXY (inverse via EURUSD): EUR falling → USD strong → risk-off → short BTC
        if "eurusd_close" in tf.columns:
            eur = tf["eurusd_close"].astype(float)
            eur_mom = eur.pct_change(p["dxy_window"]).fillna(0)
            # EUR rising → weak dollar → BTC bullish
            sig_dxy = np.tanh(eur_mom * 50)  # scale for tanh sensitivity
            signals.append(sig_dxy)
            weights.append(p["dxy_weight"])

        # 2. Gold momentum: gold rising → mixed, but often BTC follows in risk regimes
        if "gold_close" in tf.columns:
            gold = tf["gold_close"].astype(float)
            gold_mom = gold.pct_change(p["gold_window"]).fillna(0)
            sig_gold = np.tanh(gold_mom * 30)
            signals.append(sig_gold)
            weights.append(p["gold_weight"])

        # 3. BTC-Gold rolling correlation: when highly correlated → "macro risk" regime
        if "gold_close" in tf.columns:
            gold = tf["gold_close"].astype(float)
            btc_ret = close.pct_change().fillna(0)
            gold_ret = gold.pct_change().fillna(0)
            rolling_corr = btc_ret.rolling(p["corr_window"], min_periods=p["corr_window"] // 2).corr(gold_ret)
            # High positive correlation → BTC follows gold → use gold momentum as BTC signal
            # Negative correlation → BTC moves opposite to gold
            sig_corr = rolling_corr.fillna(0) * np.tanh(gold.pct_change(48).fillna(0) * 30)
            signals.append(sig_corr)
            weights.append(p["corr_weight"])

        if not signals:
            return pd.Series(0.0, index=df.index)

        total_w = sum(weights)
        combined = pd.Series(0.0, index=df.index)
        for sig, w in zip(signals, weights):
            combined += (w / total_w) * sig.fillna(0)

        if p["smooth"] > 1:
            combined = combined.rolling(p["smooth"], min_periods=1).mean()

        return combined.clip(-1, 1).fillna(0)
