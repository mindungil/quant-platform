"""Tests for online learning primitives."""
from __future__ import annotations

import numpy as np

from shared.ml.online import OnlineRidge, RecursiveLeastSquares
from shared.ml.uniqueness import average_uniqueness


def test_online_ridge_recovers_linear_pattern():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((400, 4))
    true_w = np.array([0.5, -0.3, 1.1, 0.0])
    y = X @ true_w + 0.05 * rng.standard_normal(400)
    model = OnlineRidge(n_features=4, alpha=1e-4)
    for i in range(len(X)):
        model.update(X[i], y[i])
    pred = np.array([model.predict(X[i]) for i in range(50)])
    err = float(np.abs(pred - y[:50]).mean())
    assert err < 0.2


def test_online_ridge_batch_fit_and_predict():
    rng = np.random.default_rng(1)
    X = rng.standard_normal((300, 3))
    y = X[:, 0] - 2 * X[:, 1]
    model = OnlineRidge(n_features=3, alpha=1e-3)
    model.fit_batch(X, y)
    pred = np.array([model.predict(X[i]) for i in range(20)])
    assert np.allclose(pred, y[:20], atol=0.2)


def test_rls_adapts_to_drift():
    """Verify RLS forgets old regime when forgetting < 1."""
    rng = np.random.default_rng(2)
    n = 800
    X = rng.standard_normal((n, 2))
    # First half: y = +X0; second half: y = -X0
    y = np.concatenate([X[: n // 2, 0], -X[n // 2 :, 0]])
    rls = RecursiveLeastSquares(n_features=2, forgetting=0.99)
    for i in range(n):
        rls.update(X[i], y[i])
    # After adaptation, weight on X0 should be roughly negative
    assert rls.weights[0] < 0.0


def test_rls_with_no_forgetting_matches_ols():
    rng = np.random.default_rng(3)
    X = rng.standard_normal((200, 3))
    true_w = np.array([1.0, -0.5, 0.3])
    y = X @ true_w
    rls = RecursiveLeastSquares(n_features=3, forgetting=1.0, init_var=1e6)
    for i in range(len(X)):
        rls.update(X[i], y[i])
    pred = np.array([rls.predict(X[i]) for i in range(20)])
    assert np.allclose(pred, y[:20], atol=0.05)


def test_average_uniqueness_basic():
    # 3 events: [0..5], [3..8], [10..15]. Bars 3,4,5 are concurrent (2 events).
    t0 = np.array([0, 3, 10])
    t1 = np.array([5, 8, 15])
    w = average_uniqueness(t0, t1, n_bars=20)
    assert len(w) == 3
    # Event 0 and 1 should have lower uniqueness than event 2 (no overlap)
    assert w[2] > w[0]
    assert w[2] > w[1]
    # All weights in (0, 1]
    assert (w > 0).all() and (w <= 1.0).all()


def test_average_uniqueness_no_overlap_returns_one():
    t0 = np.array([0, 10])
    t1 = np.array([5, 15])
    w = average_uniqueness(t0, t1, n_bars=20)
    assert np.allclose(w, 1.0)
