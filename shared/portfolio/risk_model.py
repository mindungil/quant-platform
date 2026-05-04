"""PCA-based factor risk model + Brinson-style PnL attribution.

Barra/Axioma-grade risk models run hundreds of fundamental/style factors;
for a crypto-focused multi-asset portfolio we don't have analogous factors,
so we use **statistical factors** extracted by PCA of the covariance matrix.
This still gives us:

  - Factor exposures (β_i,k) per asset to each factor
  - Factor risk contribution: w'·Σ_f·w vs idiosyncratic w'·Σ_ε·w
  - PnL decomposition into factor and idio components

For attribution, we use a simplified Brinson approach: decompose the
portfolio's PnL vs. a benchmark into allocation + selection effects.

Purpose: when a strategy drawdown hits, this module tells you whether
the loss was *systematic* (factor beta) or *idiosyncratic* (specific),
which determines the right response (de-risk vs. investigate the alpha).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FactorModel:
    factor_loadings: np.ndarray   # (N assets, K factors)
    factor_returns: np.ndarray    # (T bars, K factors)
    specific_returns: np.ndarray  # (T, N) residuals
    factor_cov: np.ndarray        # (K, K)
    specific_var: np.ndarray      # (N,) diagonal of idio cov
    explained_variance: np.ndarray  # per-factor (K,)
    asset_names: list[str]

    def risk_decomposition(self, weights: np.ndarray) -> dict:
        """Split portfolio variance into factor + specific components."""
        w = np.asarray(weights, dtype=float)
        factor_exposure = self.factor_loadings.T @ w   # (K,)
        factor_var = float(factor_exposure @ self.factor_cov @ factor_exposure)
        specific_var = float((w ** 2) @ self.specific_var)
        total = factor_var + specific_var
        return {
            "factor_variance": factor_var,
            "specific_variance": specific_var,
            "total_variance": total,
            "factor_share": factor_var / total if total > 0 else 0.0,
            "factor_exposures": {
                f"F{k+1}": float(factor_exposure[k])
                for k in range(len(factor_exposure))
            },
        }

    def attribute_pnl(self, weights: np.ndarray) -> dict:
        """PnL time series split into factor + specific contributions."""
        w = np.asarray(weights, dtype=float)
        factor_exposure = self.factor_loadings.T @ w  # (K,)
        factor_pnl = self.factor_returns @ factor_exposure
        specific_pnl = self.specific_returns @ w
        total = factor_pnl + specific_pnl
        def _fmt(x):
            return {
                "sum": float(np.sum(x)),
                "mean": float(np.mean(x)),
                "std": float(np.std(x, ddof=1)) if len(x) > 1 else 0.0,
            }
        return {
            "factor": _fmt(factor_pnl),
            "specific": _fmt(specific_pnl),
            "total": _fmt(total),
            "factor_share_of_return": (
                float(np.sum(factor_pnl) / np.sum(total))
                if np.sum(total) != 0 else 0.0
            ),
        }


def fit_pca_factor_model(
    asset_returns: pd.DataFrame,
    n_factors: int = 3,
) -> FactorModel:
    """Fit a PCA factor model to asset returns.

    `asset_returns` is a (T, N) DataFrame indexed by time. Returns a
    FactorModel with K = min(n_factors, N-1, T-1) factors.
    """
    R = asset_returns.to_numpy().astype(float)
    R = np.nan_to_num(R, nan=0.0)
    T, N = R.shape
    K = int(min(n_factors, N, max(T - 1, 1)))
    # Demean
    mu = R.mean(axis=0)
    Rc = R - mu
    # Asset covariance
    cov = np.cov(Rc, rowvar=False)
    # Eigen-decompose
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Sort descending
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    loadings = eigvecs[:, :K]              # (N, K)
    top_vals = eigvals[:K]
    # Factor returns: project centered returns onto loadings
    factor_returns = Rc @ loadings         # (T, K)
    # Specific returns = residual after removing factor contribution
    reconstruction = factor_returns @ loadings.T  # (T, N)
    specific_returns = Rc - reconstruction
    specific_var = np.var(specific_returns, axis=0, ddof=1)
    factor_cov = np.diag(top_vals)         # orthogonal factors
    explained = top_vals / (eigvals.sum() or 1.0)
    return FactorModel(
        factor_loadings=loadings,
        factor_returns=factor_returns,
        specific_returns=specific_returns,
        factor_cov=factor_cov,
        specific_var=specific_var,
        explained_variance=explained,
        asset_names=list(asset_returns.columns),
    )


def brinson_attribution(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    asset_returns: dict[str, float],
    benchmark_return: float,
    benchmark_asset_returns: dict[str, float] | None = None,
) -> dict:
    """Brinson-Fachler allocation + selection + interaction attribution.

    - Allocation  = Σ (w_p,i − w_b,i) · (r_b,i − r_b)
    - Selection   = Σ w_b,i · (r_p,i − r_b,i)
    - Interaction = Σ (w_p,i − w_b,i) · (r_p,i − r_b,i)

    Parameters
    ----------
    portfolio_weights : per-asset portfolio weights (sum ~1)
    benchmark_weights : per-asset benchmark weights (sum ~1)
    asset_returns     : per-asset portfolio returns (r_p,i)
    benchmark_return  : total benchmark return (scalar r_b)
    benchmark_asset_returns : per-asset benchmark returns (r_b,i).
        If None, defaults to ``asset_returns`` (same-asset universe),
        which collapses selection and interaction to zero by construction.
    """
    if benchmark_asset_returns is None:
        benchmark_asset_returns = asset_returns

    keys = (
        set(portfolio_weights)
        | set(benchmark_weights)
        | set(asset_returns)
        | set(benchmark_asset_returns)
    )
    allocation = 0.0
    selection = 0.0
    interaction = 0.0
    for k in keys:
        w_p = portfolio_weights.get(k, 0.0)
        w_b = benchmark_weights.get(k, 0.0)
        r_p = asset_returns.get(k, 0.0)
        r_b = benchmark_asset_returns.get(k, 0.0)
        allocation += (w_p - w_b) * (r_b - benchmark_return)
        selection += w_b * (r_p - r_b)
        interaction += (w_p - w_b) * (r_p - r_b)
    port_ret = sum(
        portfolio_weights.get(k, 0.0) * asset_returns.get(k, 0.0) for k in keys
    )
    return {
        "portfolio_return": round(port_ret, 6),
        "benchmark_return": round(benchmark_return, 6),
        "active_return": round(port_ret - benchmark_return, 6),
        "allocation_effect": round(float(allocation), 6),
        "selection_effect": round(float(selection), 6),
        "interaction_effect": round(float(interaction), 6),
    }
