"""Enhanced 4-state regime detector.

The original VolTrendRegime classified 8/9 years as "RANGE" because:
  1) 168-bar windows are too short (7 days → noise dominates)
  2) Only 2 features (trend_z, vol_z) → can't distinguish high-vol-trend
     (engine's sweet spot) from low-vol-chop (engine's worst case)
  3) Softmax over L2 distance produces near-uniform probabilities

This enhanced version uses:
  - LONGER windows: 720-bar (30d) vol, 1440-bar (60d) trend
  - 3 FEATURES: vol percentile, trend strength, return autocorrelation
  - 4 STATES designed around engine performance patterns:
      STRONG: high vol + clear trend (2019, 2020, 2026) → engine thrives
      CHOPPY: high vol + no trend (2021 H2) → dangerous
      QUIET_TREND: low vol + trend (2023-2025 bull) → middling
      DEAD: low vol + no trend → worst case

The affinity table should map:
  trend_breakout:    STRONG 1.5, CHOPPY 0.4, QUIET_TREND 0.8, DEAD 0.3
  momentum_ensemble: STRONG 1.5, CHOPPY 0.5, QUIET_TREND 1.0, DEAD 0.3
  kalman_trend:      STRONG 1.3, CHOPPY 0.6, QUIET_TREND 1.2, DEAD 0.5
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from shared.regime.detector import RegimeOutput


@dataclass
class EnhancedRegime:
    vol_window: int = 720           # 30d rolling vol
    vol_baseline: int = 24 * 365    # 1-year baseline for vol percentile
    trend_window: int = 1440        # 60d for trend detection
    autocorr_window: int = 720      # 30d for autocorrelation
    autocorr_lag: int = 24           # 1-day lag for return autocorrelation
    smooth: int = 48                 # 2-day smoothing on features
    # Thresholds
    vol_high_pct: float = 0.55       # above this percentile = "high vol"
    trend_strong: float = 0.6        # trend z above this = "clear trend"

    STATE_NAMES = ["STRONG", "CHOPPY", "QUIET_TREND", "DEAD"]

    def fit_predict(self, df: pd.DataFrame) -> RegimeOutput:
        close = df["close"].astype(float)
        log_ret = np.log(close / close.shift(1)).fillna(0.0)
        n = len(close)

        # Feature 1: Realized vol percentile (vs 1-year history)
        rv = log_ret.rolling(self.vol_window, min_periods=self.vol_window // 3).std(ddof=0)
        rv_pct = rv.rolling(self.vol_baseline, min_periods=self.vol_baseline // 4).rank(pct=True).fillna(0.5)

        # Feature 2: Trend strength = |z-score of cumulative return over trend_window|
        cum_ret = log_ret.rolling(self.trend_window, min_periods=self.trend_window // 3).sum()
        cum_mean = cum_ret.rolling(self.vol_baseline, min_periods=self.vol_baseline // 4).mean()
        cum_std = cum_ret.rolling(self.vol_baseline, min_periods=self.vol_baseline // 4).std(ddof=0).replace(0, np.nan)
        trend_z = ((cum_ret - cum_mean) / cum_std).fillna(0.0).abs()

        # Feature 3: Return autocorrelation at lag (positive = trending market)
        ret_24h = log_ret.rolling(self.autocorr_lag, min_periods=self.autocorr_lag).sum()
        autocorr = ret_24h.rolling(self.autocorr_window, min_periods=self.autocorr_window // 3).apply(
            lambda x: pd.Series(x).autocorr(lag=1) if len(x) > 2 else 0.0, raw=False
        ).fillna(0.0)

        # Smooth all features
        if self.smooth > 1:
            rv_pct = rv_pct.ewm(span=self.smooth, adjust=False).mean()
            trend_z = trend_z.ewm(span=self.smooth, adjust=False).mean()
            autocorr = autocorr.ewm(span=self.smooth, adjust=False).mean()

        # Classify: 2D grid (vol_high × trend_strong)
        vol_high = rv_pct > self.vol_high_pct
        trend_clear = trend_z > self.trend_strong

        # Labels
        labels = pd.Series(3, index=close.index, dtype=int)  # default DEAD
        labels = labels.mask(vol_high & trend_clear, 0)       # STRONG
        labels = labels.mask(vol_high & ~trend_clear, 1)      # CHOPPY
        labels = labels.mask(~vol_high & trend_clear, 2)      # QUIET_TREND

        # Soft probabilities: use feature distances to state prototypes
        # Prototypes in (rv_pct, trend_z, autocorr) space:
        centers = np.array([
            [0.75, 1.2, 0.15],   # STRONG: high vol, strong trend, positive autocorr
            [0.75, 0.2, -0.05],  # CHOPPY: high vol, no trend, slightly negative autocorr
            [0.30, 1.0, 0.10],   # QUIET_TREND: low vol, moderate trend
            [0.30, 0.2, 0.00],   # DEAD: low vol, no trend
        ])
        feats = np.column_stack([rv_pct.values, trend_z.values, autocorr.values])
        # Weighted distance (vol and trend matter more than autocorr)
        weights = np.array([2.0, 2.0, 1.0])

        proba = np.zeros((n, 4))
        for i, c in enumerate(centers):
            d = ((feats - c) ** 2 * weights).sum(axis=1)
            proba[:, i] = np.exp(-d)
        row_sum = proba.sum(axis=1, keepdims=True)
        row_sum[row_sum == 0] = 1.0
        proba = proba / row_sum

        proba_df = pd.DataFrame(proba, index=close.index, columns=self.STATE_NAMES)
        return RegimeOutput(label=labels, proba=proba_df, state_names=list(self.STATE_NAMES))
