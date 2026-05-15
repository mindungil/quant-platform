"""Pure-numpy CART regression tree + bagged Random Forest.

We avoid scikit-learn / lightgbm so the engine has zero ML dependencies.
The implementation is intentionally simple but correct: best-split CART
with squared-error gain, depth + min-samples stopping, and bagging with
feature subsampling à la Breiman 2001.

This isn't competitive with lightgbm on speed, but for ~3000 samples ×
~20 features (typical walk-forward retrain window) it's plenty fast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class _Node:
    feature: int = -1
    threshold: float = 0.0
    left: Optional["_Node"] = None
    right: Optional["_Node"] = None
    value: float = 0.0
    is_leaf: bool = True


@dataclass
class RegressionTree:
    max_depth: int = 6
    min_samples_split: int = 20
    min_samples_leaf: int = 10
    max_features: int | None = None  # None → use all
    seed: int = 0
    _root: _Node | None = field(default=None, init=False, repr=False)
    _rng: np.random.Generator | None = field(default=None, init=False, repr=False)

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RegressionTree":
        self._rng = np.random.default_rng(self.seed)
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self._root = self._build(X, y, depth=0)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        out = np.zeros(len(X))
        for i, row in enumerate(X):
            node = self._root
            while not node.is_leaf:
                if row[node.feature] <= node.threshold:
                    node = node.left
                else:
                    node = node.right
            out[i] = node.value
        return out

    # ----- internals -----
    def _build(self, X: np.ndarray, y: np.ndarray, depth: int) -> _Node:
        n, p = X.shape
        node = _Node(value=float(np.mean(y)) if len(y) > 0 else 0.0, is_leaf=True)
        if (
            depth >= self.max_depth
            or n < self.min_samples_split
            or float(np.var(y)) < 1e-10
        ):
            return node

        # Feature subsampling (random forest)
        if self.max_features is not None and self.max_features < p:
            feat_idx = self._rng.choice(p, size=self.max_features, replace=False)
        else:
            feat_idx = np.arange(p)

        best_gain = 0.0
        best_feat = -1
        best_thr = 0.0
        best_left_mask = None
        parent_var = float(np.var(y)) * n

        # Vectorized split search: O(n log n) per feature via sort + cumulative sums.
        for f in feat_idx:
            col = X[:, f]
            order = np.argsort(col, kind="quicksort")
            col_s = col[order]
            y_s = y[order]
            # Quantile-spaced candidate split positions (avoid checking every value)
            n_cands = min(8, max(2, n // (self.min_samples_leaf * 2)))
            if n_cands < 2:
                continue
            cand_positions = np.linspace(
                self.min_samples_leaf,
                n - self.min_samples_leaf,
                n_cands,
                dtype=int,
            )
            cand_positions = np.unique(cand_positions)
            cum_y = np.cumsum(y_s)
            cum_y2 = np.cumsum(y_s * y_s)
            total_y = cum_y[-1]
            total_y2 = cum_y2[-1]
            for split in cand_positions:
                if split <= 0 or split >= n:
                    continue
                # Skip when adjacent values are equal (would be ambiguous)
                if col_s[split - 1] == col_s[split]:
                    continue
                nL = split
                nR = n - split
                if nL < self.min_samples_leaf or nR < self.min_samples_leaf:
                    continue
                sumL = cum_y[split - 1]
                sum2L = cum_y2[split - 1]
                sumR = total_y - sumL
                sum2R = total_y2 - sum2L
                # SSE_left = sum2L - sumL^2 / nL
                vL = sum2L - (sumL * sumL) / nL
                vR = sum2R - (sumR * sumR) / nR
                gain = parent_var - (vL + vR)
                if gain > best_gain:
                    best_gain = gain
                    best_feat = int(f)
                    best_thr = float((col_s[split - 1] + col_s[split]) / 2.0)
                    best_left_mask = X[:, f] <= best_thr

        if best_feat < 0:
            return node

        node.is_leaf = False
        node.feature = best_feat
        node.threshold = best_thr
        node.left = self._build(X[best_left_mask], y[best_left_mask], depth + 1)
        node.right = self._build(X[~best_left_mask], y[~best_left_mask], depth + 1)
        return node


@dataclass
class RandomForestRegressor:
    n_estimators: int = 30
    max_depth: int = 6
    min_samples_split: int = 20
    min_samples_leaf: int = 10
    max_features: int | None = None  # default → sqrt(p)
    bootstrap: bool = True
    seed: int = 0
    _trees: list[RegressionTree] = field(default_factory=list, init=False, repr=False)

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RandomForestRegressor":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, p = X.shape
        max_feat = self.max_features or max(1, int(np.sqrt(p)))
        rng = np.random.default_rng(self.seed)
        self._trees = []
        for k in range(self.n_estimators):
            if self.bootstrap:
                idx = rng.integers(0, n, size=n)
                Xk = X[idx]
                yk = y[idx]
            else:
                Xk, yk = X, y
            tree = RegressionTree(
                max_depth=self.max_depth,
                min_samples_split=self.min_samples_split,
                min_samples_leaf=self.min_samples_leaf,
                max_features=max_feat,
                seed=int(rng.integers(0, 1_000_000)),
            )
            tree.fit(Xk, yk)
            self._trees.append(tree)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=float)
        if not self._trees:
            return np.zeros(len(X))
        preds = np.stack([t.predict(X) for t in self._trees], axis=0)
        return preds.mean(axis=0)
