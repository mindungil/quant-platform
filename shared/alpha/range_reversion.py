"""Range-bound regime filter + selective mean-reversion alpha.

Strategy philosophy: "확실한 상황에 확실한 수익"
- NOT a pure mean-reversion alpha (crypto MR doesn't work standalone)
- TWO modes of operation:

1. REGIME FILTER (primary value): Outputs a regime_confidence score [0, 1].
   When regime is "choppy", the ensemble can reduce trend-following exposure.
   This prevents the 31% negative-year problem from trend alphas.

2. SELECTIVE MR (secondary): In extremely clear range-bound regimes,
   takes small counter-trend positions at BB extremes with tight stops.
   Only ~10-15% of the time. Very selective = fewer losses.

Why this approach:
- Pure MR lost money on 8-year BTC/ETH backtest (SR -0.8)
- But trend alphas lose money 31% of years in choppy markets
- Better to NOT TRADE in choppy markets than to try to profit from them
- This alpha acts as a "confidence dimmer" for the ensemble
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import (
    Alpha,
    AlphaConfig,
    adx as compute_adx,
    bollinger_pctb,
    ema,
    rsi as compute_rsi,
)


class RangeReversionAlpha(Alpha):
    DEFAULT_PARAMS = {
        # Regime detection
        "adx_period": 14,
        "adx_chop_max": 20.0,      # ADX below this = choppy (no trend)
        "adx_trend_min": 30.0,     # ADX above this = strong trend
        "bb_period": 20,
        "bb_std": 2.0,
        "bb_width_lookback": 120,
        "bb_width_chop_pct": 40,   # BB width below 40th percentile = compressed

        # Choppy detection: Choppiness Index
        "chop_period": 14,
        "chop_threshold": 61.8,    # CI > 61.8 = choppy (classic threshold)

        # Selective MR parameters (only in extreme range conditions)
        "mr_enable": True,
        "mr_bb_extreme": 0.1,      # only trade when pctb < 0.1 or > 0.9
        "mr_rsi_period": 14,
        "mr_rsi_extreme_long": 25,  # very oversold
        "mr_rsi_extreme_short": 75, # very overbought
        "mr_position_scale": 0.5,  # max MR position is ±0.5 (not full ±1)

        # Regime persistence
        "min_chop_bars": 8,        # 8 bars = ~2.6 days on 8h
        "smoothing": 5,

        # Trend confirmation for ensemble boost
        "trend_boost": True,       # when regime is clearly trending, boost confidence
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="range_reversion", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _compute_choppiness_index(self, high: pd.Series, low: pd.Series,
                                   close: pd.Series, period: int) -> pd.Series:
        """Choppiness Index: 0-100. High = choppy, Low = trending.

        CI = 100 * LOG10(SUM(ATR, period) / (highest - lowest)) / LOG10(period)
        """
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr_sum = tr.rolling(period, min_periods=period).sum()
        highest = high.rolling(period, min_periods=period).max()
        lowest = low.rolling(period, min_periods=period).min()
        range_hl = (highest - lowest).replace(0, np.nan)

        ci = 100 * np.log10(atr_sum / range_hl) / np.log10(period)
        return ci.fillna(50)  # neutral default

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        high, low, close = df["high"], df["low"], df["close"]
        n = len(close)

        # === REGIME CLASSIFICATION ===

        # 1. ADX: trend strength
        adx_v = compute_adx(high, low, close, p["adx_period"])

        # 2. Choppiness Index
        ci = self._compute_choppiness_index(high, low, close, p["chop_period"])

        # 3. BB width percentile
        bb_mid = close.rolling(p["bb_period"]).mean()
        bb_upper = bb_mid + p["bb_std"] * close.rolling(p["bb_period"]).std()
        bb_lower = bb_mid - p["bb_std"] * close.rolling(p["bb_period"]).std()
        bb_width = (bb_upper - bb_lower) / close
        bb_width_pct = bb_width.rolling(p["bb_width_lookback"], min_periods=20).rank(pct=True)

        # Regime score: 0 = strong trend, 1 = very choppy
        # Multiple signals combined
        adx_chop_score = 1.0 - ((adx_v - p["adx_chop_max"]) /
                                 (p["adx_trend_min"] - p["adx_chop_max"])).clip(0, 1)
        ci_chop_score = ((ci - 38.2) / (p["chop_threshold"] - 38.2)).clip(0, 1)
        bb_chop_score = 1.0 - bb_width_pct.clip(0, 1)

        # Weighted combination
        chop_score = (0.4 * adx_chop_score + 0.35 * ci_chop_score + 0.25 * bb_chop_score)
        chop_score = chop_score.clip(0, 1)

        # Require persistence: choppy for N consecutive bars.
        # Vectorized consecutive-True count: subtract the running max
        # of (i * ~is_choppy) from i+1 to reset on any non-choppy bar.
        is_choppy = (chop_score > 0.6)
        idx = np.arange(n)
        non_chop_idx = pd.Series(np.where(is_choppy, -1, idx), index=close.index)
        last_break = non_chop_idx.cummax()
        chop_streak = pd.Series(idx - last_break.to_numpy(), index=close.index).clip(lower=0)
        chop_confirmed = (chop_streak >= p["min_chop_bars"]).astype(float)

        # === POSITION LOGIC ===
        # In choppy regime: reduce overall exposure (negative signal to counter trend alphas)
        # The key insight: this alpha outputs a DAMPING signal

        position = pd.Series(0.0, index=close.index)

        if p.get("mr_enable", True):
            # Selective MR: only at extreme BB levels during confirmed chop
            pctb = bollinger_pctb(close, p["bb_period"], p["bb_std"])
            rsi_v = compute_rsi(close, p["mr_rsi_period"])

            extreme_low = p["mr_bb_extreme"]
            extreme_high = 1.0 - extreme_low

            # Long signal: pctb very low + RSI very low + choppy
            long_cond = ((pctb < extreme_low) &
                         (rsi_v < p["mr_rsi_extreme_long"]) &
                         (chop_confirmed > 0))
            long_strength = ((extreme_low - pctb) / extreme_low).clip(0, 1)

            # Short signal: pctb very high + RSI very high + choppy
            short_cond = ((pctb > extreme_high) &
                          (rsi_v > p["mr_rsi_extreme_short"]) &
                          (chop_confirmed > 0))
            short_strength = ((pctb - extreme_high) / extreme_low).clip(0, 1)

            mr_signal = pd.Series(0.0, index=close.index)
            mr_signal = mr_signal.where(~long_cond, long_strength * p["mr_position_scale"])
            mr_signal = mr_signal.where(~short_cond, -short_strength * p["mr_position_scale"])

            position = mr_signal

        # Smooth
        if p["smoothing"] > 1:
            position = position.ewm(span=p["smoothing"], adjust=False, min_periods=1).mean()

        return position.clip(-1, 1).fillna(0.0)

    def get_regime_score(self, df: pd.DataFrame) -> pd.Series:
        """Return choppiness score [0=trending, 1=choppy] for ensemble use.

        Other alphas can use this to scale down their positions in choppy regimes.
        """
        p = self.config.params
        high, low, close = df["high"], df["low"], df["close"]

        adx_v = compute_adx(high, low, close, p["adx_period"])
        ci = self._compute_choppiness_index(high, low, close, p["chop_period"])

        bb_mid = close.rolling(p["bb_period"]).mean()
        bb_std = close.rolling(p["bb_period"]).std()
        bb_width = (2 * p["bb_std"] * bb_std) / close
        bb_width_pct = bb_width.rolling(p["bb_width_lookback"], min_periods=20).rank(pct=True)

        adx_chop = 1.0 - ((adx_v - p["adx_chop_max"]) /
                           (p["adx_trend_min"] - p["adx_chop_max"])).clip(0, 1)
        ci_chop = ((ci - 38.2) / (p["chop_threshold"] - 38.2)).clip(0, 1)
        bb_chop = 1.0 - bb_width_pct.clip(0, 1)

        score = (0.4 * adx_chop + 0.35 * ci_chop + 0.25 * bb_chop).clip(0, 1)
        return score.fillna(0.5)
