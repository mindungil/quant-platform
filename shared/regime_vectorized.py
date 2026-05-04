"""Vectorized regime detection for alpha ensemble pipeline.

Unlike the stateful RegimeDetector in shared/regime.py (designed for
per-bar streaming updates), this module operates on full DataFrames
for backtesting and meta-engine evaluation.

Produces 6 regime labels:
  TREND_UP, TREND_DOWN, RANGE, CRISIS, MEAN_REVERT, BREAKOUT

Uses multiple orthogonal signals:
  - ADX for trend strength
  - ATR% for volatility regime
  - Hurst exponent proxy for trending vs mean-reverting
  - Bollinger squeeze for breakout detection
  - Return autocorrelation for persistence
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _adx_vectorized(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Vectorized ADX computation."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr_v = tr.ewm(alpha=1.0 / period, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr_v.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr_v.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1.0 / period, min_periods=period).mean().fillna(0.0)
    return adx_val, plus_di.fillna(0), minus_di.fillna(0)


def _hurst_proxy(log_ret: pd.Series, window: int = 100) -> pd.Series:
    """Vectorized Hurst exponent proxy via rescaled range (R/S).

    H > 0.5 → trending (persistent), H < 0.5 → mean-reverting.

    Vectorized by avoiding rolling().apply() — uses a strided view
    trick for ~200x speedup vs Python-level apply.
    """
    arr = log_ret.fillna(0.0).to_numpy(dtype=np.float64)
    n = len(arr)
    if n < window:
        return pd.Series(0.5, index=log_ret.index)

    out = np.full(n, 0.5, dtype=np.float64)
    min_p = window // 2
    log_w = np.log(window)

    # Rolling means via cumsum (O(n))
    cs = np.concatenate([[0.0], np.cumsum(arr)])
    means = (cs[window:] - cs[:-window]) / window  # shape (n-window+1,)

    # For each end-index i >= window-1, compute R/S on window ending at i.
    # Use strided array to avoid copying.
    from numpy.lib.stride_tricks import sliding_window_view
    windows = sliding_window_view(arr, window)  # (n-window+1, window)
    # Deviation from rolling mean
    dev = windows - means[:, None]
    # Cumulative deviation per window
    cumdev = np.cumsum(dev, axis=1)
    r = cumdev.max(axis=1) - cumdev.min(axis=1)
    # Std per window
    s = windows.std(axis=1, ddof=1)
    # R/S ratio → Hurst
    ratio = np.where((s > 1e-12) & (r > 1e-12), r / s, 1.0)
    h = np.log(ratio) / log_w
    h = np.clip(h, 0.0, 1.0)

    out[window - 1:] = h
    # For min_p <= i < window - 1, leave at 0.5 default
    return pd.Series(out, index=log_ret.index)


def _bollinger_squeeze(close: pd.Series, bb_period: int = 20, kc_period: int = 20, kc_mult: float = 1.5) -> pd.Series:
    """Bollinger-inside-Keltner squeeze detector. Returns 1 when in squeeze.

    Fully vectorized — no apply() calls.
    """
    bb_mid = close.rolling(bb_period, min_periods=bb_period).mean()
    bb_std = close.rolling(bb_period, min_periods=bb_period).std(ddof=0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    # Use abs diff as proxy ATR (close-based only, no HL since we don't have it here).
    diff = close.diff().abs()
    kc_atr = diff.ewm(span=kc_period, min_periods=kc_period).mean()
    kc_mid = close.ewm(span=kc_period, adjust=False, min_periods=kc_period).mean()
    kc_upper = kc_mid + kc_mult * kc_atr
    kc_lower = kc_mid - kc_mult * kc_atr

    squeeze = ((bb_upper < kc_upper) & (bb_lower > kc_lower)).astype(float)
    return squeeze.fillna(0)


def _return_autocorr(log_ret: pd.Series, window: int = 48) -> pd.Series:
    """Vectorized rolling lag-1 autocorrelation of returns.

    Uses the identity: corr(x_t, x_{t-1}) = cov(x_t, x_{t-1}) / (std_x * std_xlag)
    computed via rolling sums for ~100x speedup vs apply().
    """
    x = log_ret.fillna(0.0)
    x_lag = x.shift(1).fillna(0.0)
    xy = (x * x_lag).rolling(window, min_periods=window // 2).mean()
    mx = x.rolling(window, min_periods=window // 2).mean()
    my = x_lag.rolling(window, min_periods=window // 2).mean()
    vx = x.rolling(window, min_periods=window // 2).std(ddof=0)
    vy = x_lag.rolling(window, min_periods=window // 2).std(ddof=0)
    denom = (vx * vy).replace(0, np.nan)
    ac = (xy - mx * my) / denom
    return ac.fillna(0.0)


def classify_regime(
    df: pd.DataFrame,
    *,
    adx_period: int = 14,
    hurst_window: int = 100,
    atr_crisis_threshold: float = 0.03,
    adx_trend_threshold: float = 22.0,
    hurst_mr_threshold: float = 0.40,
    squeeze_bars_threshold: int = 3,
) -> pd.Series:
    """Classify each bar into a regime label.

    Returns a categorical Series with values:
      TREND_UP, TREND_DOWN, RANGE, CRISIS, MEAN_REVERT, BREAKOUT

    Priority (highest to lowest):
      1. CRISIS — ATR% > crisis threshold (extreme volatility)
      2. BREAKOUT — squeeze just released (Bollinger exits Keltner)
      3. TREND_UP / TREND_DOWN — ADX above threshold + directional
      4. MEAN_REVERT — Hurst < 0.40 + negative autocorrelation
      5. RANGE — default
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    log_ret = np.log(close / close.shift(1))

    # Component signals
    adx_val, plus_di, minus_di = _adx_vectorized(high, low, close, adx_period)

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr_pct = tr.ewm(span=14, min_periods=14).mean() / close

    hurst = _hurst_proxy(log_ret, hurst_window)
    squeeze = _bollinger_squeeze(close)
    autocorr = _return_autocorr(log_ret, 48)

    # Squeeze release: was in squeeze, now not
    squeeze_count = squeeze.rolling(squeeze_bars_threshold + 1, min_periods=1).sum()
    squeeze_release = (squeeze == 0) & (squeeze.shift(1) == 1) & (squeeze_count.shift(1) >= squeeze_bars_threshold)
    # Extend breakout label for a few bars after release
    breakout = squeeze_release.rolling(6, min_periods=1).max().fillna(0).astype(bool)

    # Build labels (priority order)
    labels = pd.Series("RANGE", index=df.index, dtype=object)

    # Mean-reversion regime
    mr_mask = (hurst < hurst_mr_threshold) & (autocorr < -0.05) & (adx_val < adx_trend_threshold)
    labels[mr_mask] = "MEAN_REVERT"

    # Trending regime
    trend_mask = adx_val >= adx_trend_threshold
    up_mask = trend_mask & (plus_di > minus_di)
    dn_mask = trend_mask & (minus_di >= plus_di)
    labels[up_mask] = "TREND_UP"
    labels[dn_mask] = "TREND_DOWN"

    # Breakout (overrides trend if just released)
    labels[breakout] = "BREAKOUT"

    # Crisis (highest priority)
    crisis_mask = atr_pct >= atr_crisis_threshold
    labels[crisis_mask] = "CRISIS"

    return labels
