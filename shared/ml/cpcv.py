"""Combinatorial Purged Cross-Validation (López de Prado AFML Ch. 12).

Standard K-fold gives one OOS path. CPCV partitions data into N groups
and tests on every k-of-N combination, producing C(N, k) different OOS
paths. This is critical for computing a robust Deflated Sharpe Ratio
because it gives a sample of OOS Sharpe distributions instead of a
single point estimate, dramatically reducing backtest overfitting.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np


@dataclass
class CombinatorialPurgedCV:
    n_groups: int = 6
    n_test_groups: int = 2
    embargo: int = 10

    def split(self, n_samples: int):
        """Yield (train_idx, test_idx) for every k-of-N combination."""
        idx = np.arange(n_samples)
        group_size = n_samples // self.n_groups
        groups = []
        for g in range(self.n_groups):
            start = g * group_size
            end = (g + 1) * group_size if g < self.n_groups - 1 else n_samples
            groups.append((start, end))

        for combo in combinations(range(self.n_groups), self.n_test_groups):
            test_mask = np.zeros(n_samples, dtype=bool)
            for g in combo:
                s, e = groups[g]
                test_mask[s:e] = True
            train_mask = ~test_mask
            # Apply embargo around each test group
            for g in combo:
                s, e = groups[g]
                emb_end = min(n_samples, e + self.embargo)
                train_mask[e:emb_end] = False
                train_mask[max(0, s - self.embargo) : s] = False
            yield idx[train_mask], idx[test_mask]

    def n_paths(self) -> int:
        from math import comb
        return comb(self.n_groups, self.n_test_groups)
