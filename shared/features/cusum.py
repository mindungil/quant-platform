"""CUSUM symmetric event filter (López de Prado AFML 2.5.2.1).

Sample bars only when cumulative log-return crosses ±h. Produces
event-driven sampling that focuses ML labels on periods of structural
movement and ignores quiet noise.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def cusum_filter(series: pd.Series, h: float) -> pd.DatetimeIndex:
    """Symmetric CUSUM filter on log returns.

    Args:
        series: price series (level, not returns)
        h: threshold (in log-return units)
    Returns:
        DatetimeIndex of event timestamps
    """
    if len(series) < 2:
        return pd.DatetimeIndex([])
    log_p = np.log(series.astype(float).replace(0, np.nan)).dropna()
    diff = log_p.diff().fillna(0.0).values
    s_pos = 0.0
    s_neg = 0.0
    events: list = []
    idx = log_p.index
    for i in range(1, len(diff)):
        s_pos = max(0.0, s_pos + diff[i])
        s_neg = min(0.0, s_neg + diff[i])
        if s_neg < -h:
            s_neg = 0.0
            events.append(idx[i])
        elif s_pos > h:
            s_pos = 0.0
            events.append(idx[i])
    return pd.DatetimeIndex(events)


def vol_cusum_filter(series: pd.Series, span: int = 100, k: float = 2.0) -> pd.DatetimeIndex:
    """CUSUM with adaptive threshold = k × EWMA volatility of log returns."""
    log_p = np.log(series.astype(float).replace(0, np.nan)).dropna()
    if len(log_p) < span + 2:
        return pd.DatetimeIndex([])
    rets = log_p.diff().fillna(0.0)
    vol = rets.ewm(span=span, adjust=False, min_periods=span).std().fillna(0.0)
    h_arr = (k * vol).values
    diff = rets.values
    s_pos = 0.0
    s_neg = 0.0
    events: list = []
    idx = log_p.index
    for i in range(span, len(diff)):
        h = h_arr[i] if h_arr[i] > 1e-9 else 1e-3
        s_pos = max(0.0, s_pos + diff[i])
        s_neg = min(0.0, s_neg + diff[i])
        if s_neg < -h:
            s_neg = 0.0
            events.append(idx[i])
        elif s_pos > h:
            s_pos = 0.0
            events.append(idx[i])
    return pd.DatetimeIndex(events)
