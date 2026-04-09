"""Tests for regime detectors."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.regime import HMMRegime, VolTrendRegime
from shared.backtest.synthetic import generate_synthetic_ohlcv


@pytest.fixture
def trending_df():
    return generate_synthetic_ohlcv(n_bars=3000, seed=7, trend_strength=8.0)


# ----- VolTrendRegime -----


def test_vol_trend_classifies_all_bars(trending_df):
    out = VolTrendRegime().fit_predict(trending_df)
    assert len(out.label) == len(trending_df)
    assert out.label.between(0, 3).all()
    assert len(out.state_names) == 4


def test_vol_trend_proba_rows_sum_to_one(trending_df):
    out = VolTrendRegime().fit_predict(trending_df)
    sums = out.proba.sum(axis=1)
    assert np.allclose(sums.values, 1.0, atol=1e-9)


def test_vol_trend_uptrend_detected():
    # Construct a clear uptrend
    rng = np.random.default_rng(0)
    n = 1500
    rets = 0.002 + 0.005 * rng.standard_normal(n)
    closes = np.exp(np.cumsum(rets)) * 30000
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes, "volume": np.full(n, 1000.0),
    }, index=pd.date_range("2020-01-01", periods=n, freq="1h"))

    out = VolTrendRegime(vol_window=100, trend_window=100).fit_predict(df)
    # Last 200 bars should have a heavy lean toward TREND_UP (state 0)
    final = out.label.iloc[-200:]
    most_common = final.value_counts().idxmax()
    assert most_common in {0, 2}  # TREND_UP or RANGE (not crisis or down)


# ----- HMMRegime -----


def test_hmm_runs_and_emits_proba(trending_df):
    out = HMMRegime(n_states=2, max_iter=15).fit_predict(trending_df)
    assert len(out.label) == len(trending_df)
    assert out.proba.shape[1] == 2
    sums = out.proba.iloc[1:].sum(axis=1)  # row 0 is the dropped first bar
    assert np.allclose(sums.values, 1.0, atol=1e-6)


def test_hmm_separates_high_and_low_vol():
    # Two clear regimes: 1500 calm + 1500 stormy
    rng = np.random.default_rng(11)
    calm = 0.001 * rng.standard_normal(1500)
    stormy = 0.02 * rng.standard_normal(1500)
    rets = np.concatenate([calm, stormy])
    closes = np.exp(np.cumsum(rets)) * 30000
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes, "volume": np.full(3000, 1000.0),
    }, index=pd.date_range("2020-01-01", periods=3000, freq="1h"))

    out = HMMRegime(n_states=2, max_iter=20, seed=0).fit_predict(df)
    early = out.label.iloc[100:1400].mode()[0]
    late = out.label.iloc[1600:2900].mode()[0]
    # The two halves should mostly be classified into different states
    assert early != late
