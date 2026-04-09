"""Nested Clustered Optimization (López de Prado 2019).

Improves on classical Markowitz and HRP by:
  1) DENOISING the covariance matrix via Marchenko-Pastur eigenvalue
     truncation. The bulk of small eigenvalues (random noise) is
     replaced by their average; only signal eigenvalues survive.
  2) Two-stage clustering: cluster correlated assets, optimize within
     clusters, then optimize across cluster aggregates. Robust to
     numerical instability of inverting low-rank matrices.

Use NCO when you have many noisy alpha streams whose correlation
matrix is unstable. NCO weights are more diversified and less prone
to extreme leverage than mean-variance optimization.

References:
- López de Prado 2019, "A Robust Estimator of the Efficient Frontier"
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3469961
- Laloux et al. 1999, "Noise dressing of financial correlation matrices"
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def marchenko_pastur_max(n_obs: int, n_features: int) -> float:
    """Upper bound of Marchenko-Pastur distribution for q = N/T.

    Eigenvalues above lambda_max are signal; below are noise.
    """
    q = n_features / n_obs
    return (1.0 + np.sqrt(q)) ** 2


def denoise_corr(corr: np.ndarray, n_obs: int) -> np.ndarray:
    """Replace noise eigenvalues with their average.

    Steps:
      eigvals, eigvecs = eigh(corr)
      lambda_max  = MP upper bound
      keep top eigenvalues, replace rest with their mean
      reconstruct corr; rescale diagonal to 1.
    """
    n = corr.shape[0]
    eigvals, eigvecs = np.linalg.eigh(corr)
    # eigh returns ascending; we want descending for clarity
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]

    lam_max = marchenko_pastur_max(n_obs=n_obs, n_features=n)
    n_signal = int((eigvals > lam_max).sum())
    n_signal = max(1, n_signal)
    if n_signal >= n:
        return corr
    # Average the noise eigenvalues
    noise_avg = float(eigvals[n_signal:].mean())
    eigvals_d = eigvals.copy()
    eigvals_d[n_signal:] = noise_avg
    corr_d = (eigvecs * eigvals_d) @ eigvecs.T
    # Re-normalize diagonal to 1
    d = np.sqrt(np.diag(corr_d))
    corr_d = corr_d / np.outer(d, d)
    np.fill_diagonal(corr_d, 1.0)
    return corr_d


def cov_to_corr(cov: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Decompose covariance into (correlation, std)."""
    std = np.sqrt(np.diag(cov))
    std_safe = np.where(std > 1e-12, std, 1.0)
    corr = cov / np.outer(std_safe, std_safe)
    np.fill_diagonal(corr, 1.0)
    return corr, std


def _cluster_corr(corr: np.ndarray, max_clusters: int = 6) -> list[list[int]]:
    """Simple correlation clustering: greedy nearest-neighbour merging.

    Avoids scipy dependency for the basic case. For richer clustering
    use scipy.cluster.hierarchy + fcluster.
    """
    n = corr.shape[0]
    # Distance: sqrt((1 - corr) / 2)
    dist = np.sqrt(np.clip((1.0 - corr) / 2.0, 0.0, 1.0))
    np.fill_diagonal(dist, np.inf)
    clusters: list[list[int]] = [[i] for i in range(n)]
    while len(clusters) > max_clusters:
        best_pair = (0, 1)
        best_d = np.inf
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                # average linkage
                ci = clusters[i]
                cj = clusters[j]
                sub = dist[np.ix_(ci, cj)]
                d = float(np.mean(sub))
                if d < best_d:
                    best_d = d
                    best_pair = (i, j)
        i, j = best_pair
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    return clusters


def _ivp_weights(cov: np.ndarray) -> np.ndarray:
    """Inverse-variance portfolio weights."""
    iv = 1.0 / np.diag(cov).clip(1e-12)
    return iv / iv.sum()


@dataclass
class NCOConfig:
    max_clusters: int = 6
    denoise: bool = True
    n_obs_for_denoise: int | None = None  # if None, use cov.shape[0] * 4 as proxy
    # Skip Marchenko-Pastur denoising when N is small. With ~8 alphas, MP
    # truncation is too aggressive (drops most eigenvalues as 'noise') and
    # collapses NCO to roughly equal weights — NCO underperforms HRP in that
    # regime. The default min_assets_for_denoise threshold disables denoising
    # below it; set to 0 to always denoise.
    min_assets_for_denoise: int = 12


def nco_weights(cov: np.ndarray, config: NCOConfig | None = None) -> np.ndarray:
    """Compute NCO weights for a covariance matrix.

    Returns long-only weights summing to 1. For long-short use cases
    apply directional sign separately.
    """
    cfg = config or NCOConfig()
    n = cov.shape[0]
    if n == 0:
        return np.zeros(0)
    if n == 1:
        return np.ones(1)

    corr, std = cov_to_corr(cov)
    if cfg.denoise and n >= cfg.min_assets_for_denoise:
        n_obs = cfg.n_obs_for_denoise or max(n * 4, 200)
        corr = denoise_corr(corr, n_obs=n_obs)
        cov = corr * np.outer(std, std)

    clusters = _cluster_corr(corr, max_clusters=cfg.max_clusters)
    weights = np.zeros(n)
    cluster_aggs = []  # (cluster_idx, agg_var)
    inner_weights = []  # weights inside each cluster

    # Step 1: within-cluster IVP
    for c in clusters:
        sub_cov = cov[np.ix_(c, c)]
        w_in = _ivp_weights(sub_cov)
        inner_weights.append(w_in)
        # cluster aggregate variance
        agg_var = float(w_in @ sub_cov @ w_in)
        cluster_aggs.append(agg_var)

    # Step 2: between-cluster IVP
    iv = 1.0 / np.array(cluster_aggs).clip(1e-12)
    w_out = iv / iv.sum()

    for w_o, c, w_in in zip(w_out, clusters, inner_weights):
        for k, idx in enumerate(c):
            weights[idx] = w_o * w_in[k]

    return weights
