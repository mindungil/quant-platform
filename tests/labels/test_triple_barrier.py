"""Tests for triple barrier labeling and meta-labeling."""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.labels.triple_barrier import (
    daily_vol,
    triple_barrier_labels,
    apply_meta_label,
)


def _ramp(n=200):
    idx = pd.date_range("2020-01-01", periods=n, freq="1h")
    return pd.Series(np.linspace(100, 110, n), index=idx)


def test_daily_vol_finite():
    s = _ramp()
    v = daily_vol(s)
    assert np.isfinite(v).all()


def test_pt_hit_first_on_uptrend():
    close = _ramp(n=200)
    side = pd.Series(1.0, index=close.index)
    events = close.index[10:30]
    out = triple_barrier_labels(close, events, pt_mult=0.5, sl_mult=0.5, vertical=20, side=side)
    # Pure uptrend → wins should dominate
    assert out.bin.mean() > 0.7


def test_sl_hit_first_on_downtrend():
    n = 200
    idx = pd.date_range("2020-01-01", periods=n, freq="1h")
    close = pd.Series(np.linspace(110, 100, n), index=idx)
    side = pd.Series(1.0, index=close.index)  # long bets in a down market
    events = close.index[10:30]
    out = triple_barrier_labels(close, events, pt_mult=0.5, sl_mult=0.5, vertical=20, side=side)
    # Long bets in a down market should mostly lose
    assert out.bin.mean() < 0.3


def test_apply_meta_label_zeros_below_threshold():
    primary = pd.Series([1.0, 1.0, -1.0, -1.0])
    proba = pd.Series([0.4, 0.7, 0.45, 0.9])
    out = apply_meta_label(primary, proba, threshold=0.55)
    assert out.iloc[0] == 0.0  # below threshold
    assert out.iloc[1] > 0     # above threshold, long
    assert out.iloc[2] == 0.0  # below threshold
    assert out.iloc[3] < 0     # above threshold, short
