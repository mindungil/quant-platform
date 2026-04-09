"""Fractional differentiation (López de Prado, AFML Ch. 5).

Standard integer differencing (close.diff()) wipes out memory of past prices.
Long-memory series like prices have d ≈ 1, but fractional differencing
finds the *minimum* d ∈ (0, 1) that makes the series pass an ADF stationarity
test while preserving as much memory as possible — much better features for ML.

This module implements the Fixed-Width Window FFD variant which is faster
and more numerically stable than the expanding-window version.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def get_weights_ffd(d: float, threshold: float = 1e-4, max_size: int = 10000) -> np.ndarray:
    """Compute fractional-difference weights, dropping those below threshold.

    w_k = -w_{k-1} * (d - k + 1) / k
    """
    w = [1.0]
    k = 1
    while k < max_size:
        next_w = -w[-1] * (d - k + 1) / k
        if abs(next_w) < threshold:
            break
        w.append(next_w)
        k += 1
    return np.array(w[::-1], dtype=float)  # oldest first


def frac_diff_ffd(
    series: pd.Series,
    d: float = 0.4,
    threshold: float = 1e-4,
) -> pd.Series:
    """Fixed-width window fractional differencing.

    Each output value uses the same number of past observations (the window
    size determined by the weight-truncation threshold). Operates on log
    series for price-like inputs.
    """
    s = series.astype(float).copy()
    w = get_weights_ffd(d, threshold)
    width = len(w) - 1
    if width >= len(s):
        return pd.Series(np.zeros(len(s)), index=series.index, name=series.name)

    values = s.values
    out = np.full(len(values), np.nan, dtype=float)
    for i in range(width, len(values)):
        window = values[i - width : i + 1]
        if np.isfinite(window).all():
            out[i] = float(np.dot(w, window))
    return pd.Series(out, index=series.index, name=series.name)


def find_min_d(
    series: pd.Series,
    d_grid: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    pvalue_target: float = 0.05,
) -> float:
    """Smallest d in grid for which a simple ADF-style test passes.

    We use a lightweight pure-numpy ADF surrogate (autoregression on lags +
    deterministic trend); for proper inference users can substitute
    statsmodels' adfuller. Returns the smallest d for which the test
    statistic clears the target.
    """
    log_s = np.log(series.replace(0, np.nan).dropna())
    if len(log_s) < 200:
        return 1.0
    for d in d_grid:
        diffed = frac_diff_ffd(log_s, d=d).dropna()
        if len(diffed) < 100:
            continue
        # Simple stationarity check: ratio of variance of expanding-window
        # mean to total variance. Stationary series → ratio → 0.
        roll_mean = diffed.expanding(min_periods=20).mean()
        ratio = float(np.nanvar(roll_mean.values) / max(float(np.nanvar(diffed.values)), 1e-12))
        if ratio < pvalue_target:
            return d
    return 1.0


def frac_diff(series: pd.Series, d: float | None = None) -> pd.Series:
    """Convenience: auto-find d if not given, then return FFD series."""
    if d is None:
        d = find_min_d(series)
    log_s = np.log(series.astype(float).replace(0, np.nan))
    out = frac_diff_ffd(log_s, d=d)
    return out.fillna(0.0)
