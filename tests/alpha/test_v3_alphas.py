"""Tests for v3 alphas: KalmanTrend and MetaForest."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha import KalmanTrendAlpha, MetaForestAlpha, get_alpha
from shared.backtest.synthetic import generate_synthetic_ohlcv


@pytest.fixture
def df():
    return generate_synthetic_ohlcv(n_bars=4500, seed=7, trend_strength=8.0)


def test_kalman_trend_position_bounded(df):
    sig = KalmanTrendAlpha().generate(df)
    assert sig.position.between(-1.0, 1.0).all()
    assert len(sig.position) == len(df)


def test_kalman_trend_active_in_trend(df):
    sig = KalmanTrendAlpha().generate(df)
    # On a trending synthetic series the alpha should hold positions
    assert (sig.position.abs() > 0.1).mean() > 0.3


def test_meta_forest_runs_and_position_bounded(df):
    sig = MetaForestAlpha().generate(df)
    assert sig.position.between(-1.0, 1.0).all()
    assert len(sig.position) == len(df)


def test_meta_forest_warmup_zeros_first_bars(df):
    sig = MetaForestAlpha().generate(df)
    # First 1000 bars should all be zero (warmup)
    assert (sig.position.iloc[:1000].abs() < 1e-9).all()


def test_v3_alphas_registered():
    assert get_alpha("kalman_trend").name == "kalman_trend"
    assert get_alpha("ml_forest").name == "ml_forest"
