"""Purged + embargoed K-Fold cross-validation.

From López de Prado "Advances in Financial ML" Ch. 7. Standard K-Fold
*leaks* because overlapping labels (e.g. triple-barrier labels that span
multiple bars) mean a training label can include information from the
test period. Fix:
  1. **Purge** training samples whose label window overlaps the test
     window in either direction.
  2. **Embargo** training samples for a short window *after* the test
     period to prevent serial-correlation leakage.

This module generates (train_idx, test_idx) pairs compatible with any
sklearn-like workflow but without requiring sklearn.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PurgedKFold:
    n_splits: int = 5
    embargo_pct: float = 0.01  # fraction of total length to embargo after test

    def split(
        self,
        times: pd.Series,
        label_end_times: pd.Series | None = None,
    ):
        """Yield (train_idx, test_idx) integer arrays.

        Args:
            times: index of bar times (position i → sample i's feature time).
            label_end_times: per-sample label expiration time; if None, assume
                each sample's label expires at the next bar (no overlap).
        """
        if label_end_times is None:
            # Default: each label's window is [t_i, t_{i+1}]
            label_end_times = pd.Series(times.values, index=times.index).shift(-1).ffill()
        n = len(times)
        idx = np.arange(n)
        fold_size = n // self.n_splits
        embargo = int(n * self.embargo_pct)

        for k in range(self.n_splits):
            test_start = k * fold_size
            test_end = (k + 1) * fold_size if k < self.n_splits - 1 else n
            test_idx = idx[test_start:test_end]

            test_time_start = times.iloc[test_start]
            test_time_end = times.iloc[test_end - 1]

            # Purge: drop train samples whose label window overlaps test window
            mask = np.ones(n, dtype=bool)
            mask[test_idx] = False
            # Overlap condition: label_end[i] >= test_time_start AND times[i] <= test_time_end
            overlap = (label_end_times.values >= test_time_start) & (
                times.values <= test_time_end
            )
            mask &= ~overlap

            # Embargo: drop the next `embargo` samples after test window
            embargo_end = min(test_end + embargo, n)
            mask[test_end:embargo_end] = False

            train_idx = idx[mask]
            yield train_idx, test_idx


def combinatorial_purged_kfold(
    times: pd.Series,
    n_splits: int = 6,
    n_test_splits: int = 2,
    embargo_pct: float = 0.01,
    label_end_times: pd.Series | None = None,
):
    """López de Prado's CPCV — backtests with purging+embargo across
    *all* C(n_splits, n_test_splits) combinations. Returns pairs of
    (train_idx, test_idx).

    Key benefit: produces multiple independent OOS paths, so you can
    aggregate statistics (like DSR) across them instead of the single
    OOS estimate plain walk-forward gives.
    """
    from itertools import combinations

    if label_end_times is None:
        label_end_times = pd.Series(times.values, index=times.index).shift(-1).ffill()
    n = len(times)
    idx = np.arange(n)
    fold_size = n // n_splits
    embargo = int(n * embargo_pct)

    for combo in combinations(range(n_splits), n_test_splits):
        test_idx_list = []
        test_windows = []
        for k in combo:
            a = k * fold_size
            b = (k + 1) * fold_size if k < n_splits - 1 else n
            test_idx_list.append(idx[a:b])
            test_windows.append((times.iloc[a], times.iloc[b - 1], b))
        test_idx = np.concatenate(test_idx_list)

        mask = np.ones(n, dtype=bool)
        mask[test_idx] = False
        for t_start, t_end, b_end in test_windows:
            overlap = (label_end_times.values >= t_start) & (times.values <= t_end)
            mask &= ~overlap
            emb_end = min(b_end + embargo, n)
            mask[b_end:emb_end] = False

        train_idx = idx[mask]
        yield train_idx, test_idx
