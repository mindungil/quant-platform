"""Tests for the ML meta-alpha."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.alpha import MetaMLAlpha, get_alpha
from shared.alpha.base import AlphaConfig
from shared.alpha.ml_meta import _ridge_fit, _ridge_predict, default_feature_builder
from shared.backtest.synthetic import generate_synthetic_ohlcv


def test_ridge_fits_perfect_linear_data():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((200, 3))
    true_w = np.array([0.5, -0.3, 1.2])
    y = X @ true_w + 0.01 * rng.standard_normal(200)
    fit = _ridge_fit(X, y, alpha=1e-6)
    pred = _ridge_predict(fit, X)
    err = np.abs(pred - y).mean()
    assert err < 0.02


def test_default_features_no_nans_after_fillna():
    df = generate_synthetic_ohlcv(n_bars=2000, seed=7)
    feats = default_feature_builder(df)
    assert not feats.isna().any().any()
    assert feats.shape[0] == len(df)


def test_meta_ml_alpha_runs_and_bounds_position():
    df = generate_synthetic_ohlcv(n_bars=3500, seed=7, trend_strength=8.0)
    alpha = MetaMLAlpha()
    sig = alpha.generate(df)
    assert sig.position.between(-1.0, 1.0).all()
    assert len(sig.position) == len(df)
    # Should make at least some non-zero predictions in the post-train window
    assert (sig.position.abs() > 0.001).any()


def test_meta_ml_registered_in_alpha_registry():
    alpha = get_alpha("ml_meta")
    assert alpha.name == "ml_meta"
