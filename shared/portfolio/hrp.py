"""Hierarchical Risk Parity (López de Prado 2016).

Builds diversified portfolios without inverting the covariance matrix
(unlike Markowitz). Three steps: tree clustering, quasi-diagonalization,
recursive bisection.

Reference: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2708678

Clean-room implementation from the published algorithm. No GPL code reused.
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

logger = logging.getLogger(__name__)


def correlation_to_distance(corr: np.ndarray) -> np.ndarray:
    """Convert correlation matrix to distance matrix.

    d_ij = sqrt(0.5 * (1 - rho_ij))   in [0, 1]
    0 means perfectly correlated; 1 means perfectly anti-correlated.
    """
    corr = np.asarray(corr, dtype=float)
    # Clip for numerical safety — floating point drift can push rho slightly
    # outside [-1, 1] and make the sqrt NaN.
    clipped = np.clip(corr, -1.0, 1.0)
    dist = np.sqrt(np.maximum(0.5 * (1.0 - clipped), 0.0))
    # Force exact zero on the diagonal (scipy's squareform is strict).
    np.fill_diagonal(dist, 0.0)
    # Symmetrize to absorb any floating-point asymmetry.
    dist = 0.5 * (dist + dist.T)
    return dist


def cluster_assets(corr: np.ndarray, method: str = "single") -> np.ndarray:
    """Hierarchical clustering. Returns the scipy linkage matrix.

    Uses the condensed (upper-triangular) form of the distance matrix, as
    required by scipy.cluster.hierarchy.linkage.
    """
    dist = correlation_to_distance(corr)
    condensed = squareform(dist, checks=False)
    return linkage(condensed, method=method)


def quasi_diagonalize(link: np.ndarray) -> list[int]:
    """Return the leaf order from a linkage matrix.

    Equivalent to scipy.cluster.hierarchy.leaves_list — this is precisely
    the ordering that places similar assets next to each other, which is
    what López de Prado's quasi-diagonalization step achieves.
    """
    return [int(i) for i in leaves_list(link)]


def cluster_variance(cov: np.ndarray, indices: list[int]) -> float:
    """Inverse-variance weighted variance of a cluster (no matrix inversion).

    weights_i = (1/sigma_i^2) / sum(1/sigma_j^2)
    var_cluster = w' * Sigma_cluster * w
    """
    idx = list(indices)
    sub = cov[np.ix_(idx, idx)]
    diag = np.diag(sub)
    # Guard against zero-variance assets — fall back to equal weights there.
    inv_diag = np.where(diag > 0, 1.0 / np.where(diag > 0, diag, 1.0), 0.0)
    total = inv_diag.sum()
    if total <= 0:
        w = np.full(len(idx), 1.0 / len(idx))
    else:
        w = inv_diag / total
    return float(w @ sub @ w)


def recursive_bisection(cov: np.ndarray, sort_idx: list[int]) -> np.ndarray:
    """Recursive bisection step. Returns weights aligned with the ORIGINAL
    asset order (not the sorted order)."""
    n = cov.shape[0]
    weights = np.ones(n, dtype=float)
    clusters: list[list[int]] = [list(sort_idx)]

    while clusters:
        cluster = clusters.pop(0)
        if len(cluster) <= 1:
            continue
        mid = len(cluster) // 2
        left = cluster[:mid]
        right = cluster[mid:]

        var_left = cluster_variance(cov, left)
        var_right = cluster_variance(cov, right)
        denom = var_left + var_right
        if denom <= 0:
            alpha = 0.5
        else:
            alpha = 1.0 - var_left / denom

        for i in left:
            weights[i] *= alpha
        for i in right:
            weights[i] *= 1.0 - alpha

        if len(left) > 1:
            clusters.append(left)
        if len(right) > 1:
            clusters.append(right)

    return weights


def _clean_returns(returns: np.ndarray) -> np.ndarray:
    """Forward-fill then drop residual NaN rows."""
    arr = np.asarray(returns, dtype=float).copy()
    if arr.ndim != 2:
        raise ValueError("returns must be a 2D array (rows=time, cols=assets)")
    # Forward-fill column by column.
    for j in range(arr.shape[1]):
        col = arr[:, j]
        last = np.nan
        for i in range(col.shape[0]):
            if np.isnan(col[i]):
                col[i] = last
            else:
                last = col[i]
        arr[:, j] = col
    # Drop rows that still contain NaN (e.g. leading NaNs with no prior value).
    mask = ~np.any(np.isnan(arr), axis=1)
    return arr[mask]


def hrp_weights(
    returns: np.ndarray,
    asset_names: list[str] | None = None,
) -> dict:
    """Top-level: compute HRP weights from a returns matrix.

    Args:
        returns: 2D array with rows=time, cols=assets
        asset_names: optional names; defaults to ["asset_0", "asset_1", ...]

    Returns:
        {
            "weights": dict of asset_name -> weight (sums to 1.0),
            "ordering": list of asset_names in cluster order,
            "linkage_method": "single",
        }
    """
    arr = np.asarray(returns, dtype=float)
    if arr.ndim != 2:
        raise ValueError("returns must be 2D (rows=time, cols=assets)")

    n_assets = arr.shape[1]
    if asset_names is None:
        asset_names = [f"asset_{i}" for i in range(n_assets)]
    if len(asset_names) != n_assets:
        raise ValueError(
            f"asset_names length {len(asset_names)} != n_assets {n_assets}"
        )

    # Edge case: degenerate portfolios.
    if n_assets == 0:
        return {"weights": {}, "ordering": [], "linkage_method": "single"}
    if n_assets == 1:
        return {
            "weights": {asset_names[0]: 1.0},
            "ordering": [asset_names[0]],
            "linkage_method": "single",
        }

    cleaned = _clean_returns(arr)
    if cleaned.shape[0] < 10:
        logger.warning(
            "HRP received only %d return rows (<10); covariance estimate "
            "will be noisy.",
            cleaned.shape[0],
        )

    if cleaned.shape[0] < 2 or n_assets < 2:
        # Not enough data to estimate covariance — fall back to equal weights.
        w = 1.0 / n_assets
        return {
            "weights": {name: w for name in asset_names},
            "ordering": list(asset_names),
            "linkage_method": "single",
        }

    # Sample covariance & correlation.
    cov = np.cov(cleaned, rowvar=False)
    std = np.sqrt(np.diag(cov))
    # Avoid divide-by-zero on flat series.
    safe_std = np.where(std > 0, std, 1.0)
    corr = cov / np.outer(safe_std, safe_std)
    # Zero-vol rows become "uncorrelated" with everything.
    zero_mask = std <= 0
    if np.any(zero_mask):
        corr[zero_mask, :] = 0.0
        corr[:, zero_mask] = 0.0
        np.fill_diagonal(corr, 1.0)

    link = cluster_assets(corr, method="single")
    sort_idx = quasi_diagonalize(link)
    raw = recursive_bisection(cov, sort_idx)

    total = raw.sum()
    if total <= 0:
        norm = np.full(n_assets, 1.0 / n_assets)
    else:
        norm = raw / total

    weights = {asset_names[i]: float(norm[i]) for i in range(n_assets)}
    ordering = [asset_names[i] for i in sort_idx]

    return {
        "weights": weights,
        "ordering": ordering,
        "linkage_method": "single",
    }


__all__ = [
    "correlation_to_distance",
    "cluster_assets",
    "quasi_diagonalize",
    "cluster_variance",
    "recursive_bisection",
    "hrp_weights",
]


if __name__ == "__main__":
    # Test 1: 3 uncorrelated assets -> roughly equal weights
    np.random.seed(42)
    rets = np.random.randn(200, 3) * 0.02
    result = hrp_weights(rets, ["BTC", "ETH", "SOL"])
    print("Uncorrelated 3-asset weights:", result["weights"])
    assert abs(sum(result["weights"].values()) - 1.0) < 1e-6
    assert all(0.2 < w < 0.5 for w in result["weights"].values()), "should be roughly equal"

    # Test 2: 2 highly correlated + 1 independent -> independent gets ~0.5
    n = 200
    base = np.random.randn(n) * 0.02
    rets2 = np.column_stack([
        base + 0.001 * np.random.randn(n),
        base + 0.001 * np.random.randn(n),
        np.random.randn(n) * 0.02,
    ])
    result2 = hrp_weights(rets2, ["A_corr", "B_corr", "C_indep"])
    print("Correlated pair + independent:", result2["weights"])
    assert result2["weights"]["C_indep"] > 0.4, "independent asset should get ~50%"

    # Test 3: High vol asset gets less weight
    rets3 = np.column_stack([
        np.random.randn(200) * 0.01,  # low vol
        np.random.randn(200) * 0.05,  # high vol
    ])
    result3 = hrp_weights(rets3, ["LowVol", "HighVol"])
    print("Low vs high vol:", result3["weights"])
    assert result3["weights"]["LowVol"] > result3["weights"]["HighVol"]

    print("\nAll HRP tests passed.")
