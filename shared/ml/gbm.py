"""Gradient boosting wrapper with LightGBM primary and numpy RF fallback.

Provides a unified interface for tabular ML models used by the alpha
discovery system. LightGBM is preferred (10-50x faster, handles NaN
natively, better feature importance). Falls back to the pure-numpy
RandomForestRegressor from shared.ml.trees if lightgbm is not installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False

from shared.ml.trees import RandomForestRegressor


_DEFAULT_LGB_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "metric": "mse",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "max_depth": 6,
    "min_child_samples": 30,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "n_estimators": 200,
    "verbose": -1,
}


@dataclass
class GBMWrapper:
    """Unified gradient boosting model.

    Uses LightGBM when available, otherwise falls back to pure-numpy RF.
    """
    params: dict[str, Any] = field(default_factory=dict)
    _model: Any = field(default=None, init=False, repr=False)
    _is_lgb: bool = field(default=False, init=False, repr=False)
    _n_features: int = field(default=0, init=False, repr=False)

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        X_val: np.ndarray | None = None,
        y_val: np.ndarray | None = None,
    ) -> "GBMWrapper":
        """Train the model.

        If X_val/y_val provided and using LightGBM, early stopping is used.
        """
        X = np.asarray(X, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        self._n_features = X.shape[1]

        if HAS_LGB:
            self._is_lgb = True
            merged = {**_DEFAULT_LGB_PARAMS, **self.params}
            n_rounds = merged.pop("n_estimators", 200)

            # Use native LightGBM API (no sklearn dependency)
            if "seed" in merged:
                merged["data_random_seed"] = merged.pop("seed")
            train_data = lgb.Dataset(X, label=y, free_raw_data=False)

            valid_sets = [train_data]
            valid_names = ["train"]
            if X_val is not None and y_val is not None:
                val_data = lgb.Dataset(
                    np.asarray(X_val, dtype=np.float64),
                    label=np.asarray(y_val, dtype=np.float64),
                    free_raw_data=False,
                )
                valid_sets.append(val_data)
                valid_names.append("val")

            callbacks = [lgb.log_evaluation(period=0)]  # suppress output
            if X_val is not None:
                callbacks.append(lgb.early_stopping(20, verbose=False))

            model = lgb.train(
                merged,
                train_data,
                num_boost_round=n_rounds,
                valid_sets=valid_sets,
                valid_names=valid_names,
                callbacks=callbacks,
            )
            self._model = model
        else:
            # V14: Fallback to pure-numpy RF.
            # Argument names were wrong here (n_trees, max_features_frac)
            # — those don't exist on shared.ml.trees.RandomForestRegressor.
            # Correct names: n_estimators, max_features (int).
            # The fallback path is hit when lightgbm isn't installed
            # (CI hits this when wheel build fails on ubuntu-latest), so
            # 7 alpha tests were 100% failing whenever the fallback ran.
            n_trees = self.params.get("n_estimators", 100)
            max_depth = self.params.get("max_depth", 6)
            colsample = float(self.params.get("colsample_bytree", 0.8))
            # Convert fraction → int based on actual feature count.
            max_features_int = max(1, int(self._n_features * colsample))
            self._is_lgb = False
            rf = RandomForestRegressor(
                n_estimators=n_trees,
                max_depth=max_depth,
                min_samples_leaf=self.params.get("min_child_samples", 30),
                max_features=max_features_int,
                seed=42,
            )
            rf.fit(X, y)
            self._model = rf

        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        if self._model is None:
            raise RuntimeError("Model not fitted yet")
        return np.asarray(self._model.predict(X), dtype=np.float64)

    def feature_importance(self) -> np.ndarray:
        """Return feature importance array of shape (n_features,)."""
        if self._model is None:
            raise RuntimeError("Model not fitted yet")

        if self._is_lgb:
            return np.asarray(self._model.feature_importance(importance_type="gain"), dtype=np.float64)
        else:
            # RF doesn't track importance — return uniform
            return np.ones(self._n_features, dtype=np.float64) / max(self._n_features, 1)
