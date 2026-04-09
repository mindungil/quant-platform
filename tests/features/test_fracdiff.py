"""Tests for fractional differentiation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.features.fracdiff import frac_diff_ffd, get_weights_ffd, find_min_d


def test_get_weights_ffd_starts_at_one():
    w = get_weights_ffd(0.5, threshold=1e-5)
    # last weight (most recent obs) should be exactly 1.0
    assert w[-1] == 1.0
    assert len(w) > 1


def test_d0_returns_identity():
    s = pd.Series(np.arange(100, dtype=float) + 1)
    out = frac_diff_ffd(np.log(s), d=0.0)
    valid = out.dropna()
    # d=0 → series unchanged (window size 1)
    assert np.allclose(valid.values, np.log(s).values[-len(valid) :], atol=1e-10)


def test_d1_approximates_first_difference():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumsum(rng.standard_normal(500)) + 100)
    out = frac_diff_ffd(np.log(s), d=1.0).dropna()
    expected = np.log(s).diff().dropna().iloc[-len(out):]
    # First difference and FFD with d=1 truncated should match closely
    assert np.corrcoef(out.values[-100:], expected.values[-100:])[0, 1] > 0.99


def test_fracdiff_preserves_some_memory():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumsum(rng.standard_normal(2000)) + 100)
    log_s = np.log(s)
    fd = frac_diff_ffd(log_s, d=0.4).dropna()
    # Lag-1 autocorrelation should be smaller than the original series
    orig_ac = float(np.corrcoef(log_s.values[1:], log_s.values[:-1])[0, 1])
    fd_ac = float(np.corrcoef(fd.values[1:], fd.values[:-1])[0, 1])
    assert orig_ac > 0.99
    assert fd_ac < orig_ac


def test_find_min_d_returns_value_in_grid():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumprod(1 + 0.001 * rng.standard_normal(1000)) * 100)
    d = find_min_d(s)
    assert 0.0 <= d <= 1.0
