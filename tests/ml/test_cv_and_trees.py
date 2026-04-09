"""Tests for purged CV, CPCV, and the bagged tree learner."""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.ml.cv import PurgedKFold, purged_kfold_split
from shared.ml.cpcv import CombinatorialPurgedCV
from shared.ml.trees import RandomForestRegressor, RegressionTree


def test_purged_kfold_train_test_disjoint():
    n = 200
    for tr, te in purged_kfold_split(n, n_splits=5, embargo=5):
        assert len(set(tr) & set(te)) == 0
        assert len(te) > 0
        assert len(tr) > 0


def test_purged_kfold_uses_dataframe():
    df = pd.DataFrame({"x": np.arange(100)},
                       index=pd.date_range("2020-01-01", periods=100, freq="h"))
    pk = PurgedKFold(n_splits=5, embargo_pct=0.02)
    splits = list(pk.split(df))
    assert len(splits) == 5
    for tr, te in splits:
        assert len(set(tr) & set(te)) == 0


def test_cpcv_produces_n_combinations():
    cpcv = CombinatorialPurgedCV(n_groups=6, n_test_groups=2, embargo=5)
    splits = list(cpcv.split(n_samples=600))
    # C(6, 2) = 15
    assert len(splits) == 15
    assert cpcv.n_paths() == 15
    for tr, te in splits:
        assert len(set(tr) & set(te)) == 0


def test_regression_tree_fits_simple_data():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((300, 3))
    y = X[:, 0] - 0.5 * X[:, 1] + 0.1 * rng.standard_normal(300)
    tree = RegressionTree(max_depth=4, min_samples_leaf=20).fit(X, y)
    pred = tree.predict(X)
    r = float(np.corrcoef(pred, y)[0, 1])
    assert r > 0.5


def test_random_forest_outperforms_constant():
    rng = np.random.default_rng(0)
    X = rng.standard_normal((400, 4))
    y = np.sign(X[:, 0]) * (1 + 0.5 * X[:, 1]) + 0.1 * rng.standard_normal(400)
    rf = RandomForestRegressor(n_estimators=15, max_depth=5, min_samples_leaf=20, seed=0)
    rf.fit(X[:300], y[:300])
    pred = rf.predict(X[300:])
    r = float(np.corrcoef(pred, y[300:])[0, 1])
    assert r > 0.4
