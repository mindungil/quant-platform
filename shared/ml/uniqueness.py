"""Sample uniqueness weighting (López de Prado AFML Ch. 4).

When triple-barrier labels span overlapping bar ranges, naive equal-weight
ML training over-weights the bars that appear in many labels' horizons.
The fix: compute concurrency c_t = number of labels whose [t0_i, t1_i]
range contains bar t, then assign each sample weight = average uniqueness
along its own [t0_i, t1_i].

The result is leak-free and downweights duplicated information.
"""
from __future__ import annotations

import numpy as np


def average_uniqueness(t0_idx: np.ndarray, t1_idx: np.ndarray, n_bars: int) -> np.ndarray:
    """Compute average-uniqueness sample weights.

    Args:
        t0_idx: integer start positions of each label
        t1_idx: integer end positions of each label
        n_bars: total number of bars

    Returns:
        weights: array of length len(t0_idx), each in (0, 1].
    """
    n_events = len(t0_idx)
    if n_events == 0:
        return np.array([])
    # Concurrency: how many labels overlap each bar
    concurrency = np.zeros(n_bars, dtype=np.int32)
    for i in range(n_events):
        s = max(0, int(t0_idx[i]))
        e = min(n_bars - 1, int(t1_idx[i]))
        if e >= s:
            concurrency[s : e + 1] += 1
    concurrency = np.maximum(concurrency, 1)
    inv_conc = 1.0 / concurrency
    weights = np.zeros(n_events)
    for i in range(n_events):
        s = max(0, int(t0_idx[i]))
        e = min(n_bars - 1, int(t1_idx[i]))
        if e >= s:
            weights[i] = float(inv_conc[s : e + 1].mean())
    weights = np.where(weights > 0, weights, 1e-6)
    return weights
