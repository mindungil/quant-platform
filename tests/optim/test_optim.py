"""Tests for the parameter optimizers."""
from __future__ import annotations

import math

import numpy as np
import pytest

from shared.optim import (
    GPOptimizer,
    GPSurrogate,
    GridSearchOptimizer,
    WalkForwardObjective,
)
from shared.optim.bayes import expected_improvement, matern52
from shared.alpha import TrendBreakoutAlpha
from shared.alpha.base import AlphaConfig
from shared.backtest.synthetic import generate_synthetic_ohlcv


# ----- GP surrogate -----


def test_gp_predicts_training_points_exactly():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(20, 2))
    y = np.sin(X[:, 0] * 5) + np.cos(X[:, 1] * 3)
    gp = GPSurrogate(lengthscale=0.3, noise=1e-6)
    gp.fit(X, y)
    mean, var = gp.predict(X)
    assert np.allclose(mean, y, atol=1e-3)
    assert (var < 1e-2).all()


def test_matern52_decays():
    a = np.zeros((1, 2))
    b = np.array([[0.0, 0.0], [1.0, 0.0], [10.0, 0.0]])
    k = matern52(a, b, lengthscale=1.0)
    assert k[0, 0] == pytest.approx(1.0)
    assert k[0, 1] < k[0, 0]
    assert k[0, 2] < k[0, 1]


def test_expected_improvement_zero_for_no_uncertainty():
    mean = np.array([0.5, 0.6])
    var = np.array([1e-12, 1e-12])
    ei = expected_improvement(mean, var, y_best=0.7)
    assert (ei == 0).all()


# ----- grid search -----


def test_grid_search_finds_max():
    def f(p):
        return -(p["x"] - 3.0) ** 2 - (p["y"] - 7.0) ** 2

    opt = GridSearchOptimizer(
        objective=f,
        grid={"x": [0, 1, 2, 3, 4, 5], "y": [5, 6, 7, 8, 9]},
    )
    best, score = opt.fit()
    assert best["x"] == 3
    assert best["y"] == 7
    assert score == 0


# ----- BO -----


def test_gp_optimizer_finds_smooth_max():
    def f(p):
        # Single peak at (0.4, 0.6) in unit square
        return -((p["a"] - 0.4) ** 2 + (p["b"] - 0.6) ** 2)

    opt = GPOptimizer(
        objective=f,
        space={"a": (0.0, 1.0), "b": (0.0, 1.0)},
        n_initial=8,
        n_iter=20,
        seed=42,
    )
    best, score = opt.fit()
    # Should be within a reasonable distance of the true peak
    assert abs(best["a"] - 0.4) < 0.20
    assert abs(best["b"] - 0.6) < 0.20


def test_walk_forward_objective_runs():
    df = generate_synthetic_ohlcv(n_bars=2000, seed=7, trend_strength=8.0)

    def factory(params):
        cfg = AlphaConfig(
            name="trend_breakout",
            params={
                "donchian_window": int(round(params["donchian_window"])),
                "adx_min": float(params["adx_min"]),
            },
        )
        return TrendBreakoutAlpha(cfg)

    obj = WalkForwardObjective(
        alpha_factory=factory,
        df=df,
        n_windows=3,
        periods_per_year=24 * 365,
    )
    score = obj({"donchian_window": 40, "adx_min": 12.0})
    assert math.isfinite(score)
