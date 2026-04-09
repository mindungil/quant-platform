"""Tests for microstructure feature library."""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.features.microstructure import (
    amihud_illiquidity,
    corwin_schultz_spread,
    high_low_volatility,
    kyle_lambda,
    roll_spread,
    signed_volume,
    vpin_proxy,
)


def _make_panel(n=500, seed=0):
    rng = np.random.default_rng(seed)
    rets = 0.001 * rng.standard_normal(n)
    close = pd.Series(np.exp(np.cumsum(rets)) * 100)
    high = close * (1 + 0.001 * rng.uniform(0, 1, n))
    low = close * (1 - 0.001 * rng.uniform(0, 1, n))
    vol = pd.Series(np.abs(rng.standard_normal(n) * 100) + 50)
    return high, low, close, vol


def test_amihud_nonnegative():
    h, l, c, v = _make_panel()
    out = amihud_illiquidity(c, v, window=24)
    assert (out >= 0).all()
    assert len(out) == len(c)


def test_kyle_lambda_finite():
    h, l, c, v = _make_panel()
    out = kyle_lambda(c, v, window=48)
    assert np.isfinite(out).all()


def test_roll_spread_zero_or_positive():
    h, l, c, v = _make_panel()
    s = roll_spread(c, window=48)
    assert (s >= 0).all()


def test_corwin_schultz_finite():
    h, l, c, v = _make_panel()
    s = corwin_schultz_spread(h, l)
    assert np.isfinite(s).all()
    assert (s >= 0).all()


def test_signed_volume_sign_consistent():
    h, l, c, v = _make_panel()
    sv = signed_volume(c, v)
    diffs = c.diff().fillna(0.0)
    # whenever close went up, signed volume should be positive
    up_mask = diffs > 0
    assert (sv[up_mask] > 0).all()


def test_vpin_in_unit_interval():
    h, l, c, v = _make_panel(n=600)
    vpin = vpin_proxy(c, v, window=50)
    assert (vpin >= 0).all() and (vpin <= 1).all()


def test_parkinson_nonnegative():
    h, l, c, v = _make_panel()
    pk = high_low_volatility(h, l, window=24)
    assert (pk >= 0).all()
