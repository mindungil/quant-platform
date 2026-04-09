"""Purged K-Fold cross-validation with embargo (López de Prado AFML Ch. 7).

In time-series ML, train and test labels can overlap because each label
spans multiple bars (e.g. a triple-barrier label uses bars t..t+H). Naive
K-fold leaks information from train into test. PurgedKFold:

1) Splits indices into K contiguous folds.
2) For each test fold, *purges* training samples whose label horizon
   overlaps the test set.
3) Adds an *embargo* — drops a few extra training bars after the test
   set to absorb residual serial correlation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PurgedKFold:
    n_splits: int = 5
    embargo_pct: float = 0.01

    def split(
        self,
        X: pd.DataFrame | np.ndarray,
        t1: pd.Series | None = None,
    ):
        """Yield (train_idx, test_idx) pairs.

        Args:
            X: feature frame, indexed by event time
            t1: end-of-label times (e.g., triple-barrier touches). If
                None, falls back to identity (label ends at its own row).
        """
        n = len(X)
        idx = np.arange(n)
        fold_size = n // self.n_splits
        embargo = max(1, int(self.embargo_pct * n))

        # Build label-end-time series, defaulting to row index
        if t1 is None and isinstance(X, pd.DataFrame):
            t1_vals = np.arange(n)
        elif t1 is None:
            t1_vals = np.arange(n)
        else:
            # convert t1 timestamps to integer positions in X.index
            if isinstance(X, pd.DataFrame):
                pos = {ts: i for i, ts in enumerate(X.index)}
                t1_vals = np.array([pos.get(ts, n - 1) for ts in t1.values])
            else:
                t1_vals = t1.values.astype(int)

        for k in range(self.n_splits):
            start = k * fold_size
            end = (k + 1) * fold_size if k < self.n_splits - 1 else n
            test_idx = idx[start:end]

            # Train = everything except test ∪ overlapping ∪ embargo
            train_mask = np.ones(n, dtype=bool)
            train_mask[start:end] = False

            # Purge: drop train rows whose label-end falls inside test
            if len(t1_vals) == n:
                overlap_mask = (t1_vals >= start) & (np.arange(n) < start)
                train_mask[overlap_mask] = False

            # Embargo: drop the next embargo bars after test
            emb_end = min(n, end + embargo)
            train_mask[end:emb_end] = False

            train_idx = idx[train_mask]
            yield train_idx, test_idx


def purged_kfold_split(
    n_samples: int,
    n_splits: int = 5,
    embargo: int = 10,
):
    """Functional helper that doesn't need a DataFrame."""
    idx = np.arange(n_samples)
    fold_size = n_samples // n_splits
    for k in range(n_splits):
        start = k * fold_size
        end = (k + 1) * fold_size if k < n_splits - 1 else n_samples
        test_idx = idx[start:end]
        train_mask = np.ones(n_samples, dtype=bool)
        train_mask[start:end] = False
        emb_end = min(n_samples, end + embargo)
        train_mask[end:emb_end] = False
        train_mask[max(0, start - embargo) : start] = False
        yield idx[train_mask], test_idx
