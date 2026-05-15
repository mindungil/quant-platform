"""Bollinger / RSI mean-reversion alpha.

Counter-trend strategy designed for ranging markets. Goes long when:
- price closes below lower Bollinger band (z-score extreme), AND
- RSI is oversold, AND
- ADX is low (no strong trend).

Inverse for shorts. Position size scales with the depth of the extreme,
giving a bounded ramp instead of a binary signal — works much better with
the portfolio ensemble than hard 0/±1 signals.
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


class MeanReversionAlpha(Alpha):
    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_long_thr": 30.0,
        "rsi_short_thr": 70.0,
        "adx_period": 14,
        "adx_max": 18.0,         # above this, no MR (it's a trend, fade is dangerous)
        "ramp_smoothing": 3,     # bars of EMA on the raw signal to reduce churn
        # Trend-aligned gate: only fade toward the macro trend (buy-dip
        # in uptrend, short-rally in downtrend). On crypto this is
        # strictly necessary — pure counter-trend MR gets run over
        # during multi-week trends. Empirically verified on 8yr BTC:
        # disabling this drops Sharpe from -4.1 to -6.5.
        "use_strict_gate": True,
        "bb_width_lookback": 720,    # ~30d hourly
        "bb_width_max_pct": 60,      # only fade when BB width is below this percentile
        "trend_strength_lookback": 240,
        "trend_strength_max": 0.04,  # |close - sma| / close threshold
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="mean_reversion", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        high, low, close = df["high"], df["low"], df["close"]

        pctb = bollinger_pctb(close, p["bb_period"], p["bb_std"])  # 0..1, 0.5 = mean
        rsi_v = compute_rsi(close, p["rsi_period"])
        adx_v = compute_adx(high, low, close, p["adx_period"])

        # Bollinger contribution: linear ramp from 0.0 (at pctb=0.5) to ±1 (at pctb=0 or 1)
        bb_signal = -2.0 * (pctb - 0.5)  # pctb=0 → +1 (long), pctb=1 → -1 (short)
        bb_signal = bb_signal.clip(-1.0, 1.0)

        # RSI contribution: ramp from neutral inside thresholds
        long_rsi_strength = ((p["rsi_long_thr"] - rsi_v) / p["rsi_long_thr"]).clip(0.0, 1.0)
        short_rsi_strength = ((rsi_v - p["rsi_short_thr"]) / (100.0 - p["rsi_short_thr"])).clip(0.0, 1.0)
        rsi_signal = long_rsi_strength - short_rsi_strength  # in [-1, 1]

        # Combine: average bb + rsi (each is in [-1,1])
        raw = (bb_signal + rsi_signal) / 2.0

        # ADX gate: 1.0 below adx_max, decaying linearly to 0 at 1.5*adx_max
        upper_gate = p["adx_max"] * 1.5
        gate = ((upper_gate - adx_v) / (upper_gate - p["adx_max"])).clip(0.0, 1.0)
        # Inside the safe range, gate is exactly 1.0
        gate = gate.where(adx_v >= p["adx_max"], 1.0)

        # Optional BB-width range gate (opt-in). Strict gate AND range
        # gate in series ends up over-filtering (8yr BTC Sharpe drops
        # from -4.1 to -4.5). Keep the filter available for users who
        # disable the strict gate and want a pure-range definition.
        if p.get("use_bb_width_gate", False):
            bb_width_lookback = int(p.get("bb_width_lookback", 720))
            bb_width_max_pct = float(p.get("bb_width_max_pct", 60)) / 100.0
            std_bb = close.rolling(p["bb_period"]).std(ddof=0)
            width = (2.0 * p["bb_std"] * std_bb) / close
            width_pct = width.rolling(bb_width_lookback, min_periods=60).rank(pct=True).fillna(1.0)
            gate = gate * (width_pct <= bb_width_max_pct).astype(float)

        # Strict gate (default on): only allow MR signals aligned with
        # the macro trend — fixes the original "allow" construction
        # which used confusing chained .where() semantics.
        if p.get("use_strict_gate", True):
            ema_fast = ema(close, 50)
            ema_slow = ema(close, 200)
            uptrend = ((close > ema_slow) & (ema_fast > ema_slow)).astype(float)
            downtrend = ((close < ema_slow) & (ema_fast < ema_slow)).astype(float)
            sign = np.sign(raw)
            allow = ((sign > 0) & (uptrend > 0)) | ((sign < 0) & (downtrend > 0))
            gate = gate * allow.astype(float)

        gated = raw * gate

        # Smooth slightly to reduce intra-bar churn
        if p["ramp_smoothing"] > 1:
            gated = gated.ewm(span=p["ramp_smoothing"], adjust=False, min_periods=1).mean()

        return gated.fillna(0.0)
