"""Tests for CUSUM event filter."""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.features.cusum import cusum_filter, vol_cusum_filter


def test_no_events_when_threshold_huge():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumprod(1 + 0.001 * rng.standard_normal(500)) * 100,
                  index=pd.date_range("2020-01-01", periods=500, freq="1h"))
    events = cusum_filter(s, h=10.0)
    assert len(events) == 0


def test_some_events_with_normal_threshold():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumprod(1 + 0.005 * rng.standard_normal(2000)) * 100,
                  index=pd.date_range("2020-01-01", periods=2000, freq="1h"))
    events = cusum_filter(s, h=0.02)
    assert 5 < len(events) < 1500


def test_vol_cusum_adapts_to_vol():
    rng = np.random.default_rng(0)
    s = pd.Series(np.cumprod(1 + 0.005 * rng.standard_normal(2000)) * 100,
                  index=pd.date_range("2020-01-01", periods=2000, freq="1h"))
    events = vol_cusum_filter(s, span=100, k=2.0)
    assert len(events) > 0
