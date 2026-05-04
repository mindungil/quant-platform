"""Tests for cross-market enrichment features."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.backtest.synthetic import generate_correlated_panel, generate_synthetic_ohlcv
from shared.features.enrichment import (
    btc_dominance,
    dispersion_eigenratio,
    funding_return_divergence,
    volume_obv_regime,
)


def test_btc_dominance_in_valid_range():
    panel = generate_correlated_panel(["BTCUSDT", "ETHUSDT", "SOLUSDT"], n_bars=1500, seed=7)
    dom = btc_dominance(panel)
    # BTC dominance should live in [0, 1]
    assert dom.between(0.0, 1.0).all()
    # Should not be NaN after warmup
    assert dom.iloc[300:].notna().all()


def test_dispersion_eigenratio_bounded():
    panel = generate_correlated_panel(["A", "B", "C", "D"], n_bars=1200, seed=11)
    e = dispersion_eigenratio(panel, window=200)
    # Eigen-ratio in [1/K, 1] ≈ [0.25, 1.0] for 4 assets
    valid = e.dropna()
    assert (valid <= 1.0 + 1e-6).all()
    assert (valid >= 0.0).all()


def test_funding_divergence_zero_without_funding_column():
    df = generate_synthetic_ohlcv(n_bars=500, seed=23, funding=False)
    z = funding_return_divergence(df)
    assert (z == 0).all()


def test_funding_divergence_varies_with_funding():
    df = generate_synthetic_ohlcv(n_bars=1500, seed=23, funding=True)
    z = funding_return_divergence(df)
    # Should be a real signal — at least some bars above/below zero
    assert z.abs().max() > 0


def test_volume_obv_regime_bounded():
    df = generate_synthetic_ohlcv(n_bars=1500, seed=7, trend_strength=6.0)
    z = volume_obv_regime(df)
    assert len(z) == len(df)
    # Clipped to [-5, 5]
    assert z.between(-5.0, 5.0).all()
