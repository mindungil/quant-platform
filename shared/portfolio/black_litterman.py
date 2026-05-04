"""Black-Litterman posterior estimator.

The BL model reconciles a **market-implied equilibrium prior** with the
trader's **active views** via a Bayesian update, yielding posterior expected
returns that are the weighted combination of both. This gives allocators
stable, diversified weights even when views are concentrated — the
equilibrium prior anchors the solution.

Inputs:
    - Sigma (N×N): covariance of asset excess returns
    - w_mkt (N,): market-cap weights (the equilibrium portfolio)
    - delta: risk-aversion scalar (typical: 2.5)
    - tau: uncertainty scalar on the prior (typical: 0.025–0.05)
    - P (M×N): picking matrix — row m zeros on assets not in view m
    - Q (M,): expected view returns
    - Omega (M×M) or None: view uncertainty (diag). If None, set to
      diag(P · τΣ · Pᵀ) per He-Litterman.

Outputs:
    Posterior μ_BL and Σ_BL, plus the mean-variance optimal weights derived
    from them.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class BlackLittermanResult:
    mu_posterior: np.ndarray
    sigma_posterior: np.ndarray
    weights: np.ndarray           # mean-variance optimal post-BL
    implied_equilibrium: np.ndarray
    view_impact: dict


def reverse_optimize(
    sigma: np.ndarray,
    market_weights: np.ndarray,
    risk_aversion: float = 2.5,
) -> np.ndarray:
    """π = δ · Σ · w_mkt. Implied equilibrium excess returns."""
    return risk_aversion * sigma @ market_weights


def black_litterman(
    sigma: np.ndarray,
    market_weights: np.ndarray,
    P: np.ndarray | None = None,
    Q: np.ndarray | None = None,
    omega: np.ndarray | None = None,
    *,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
) -> BlackLittermanResult:
    Sigma = np.asarray(sigma, dtype=float)
    w_mkt = np.asarray(market_weights, dtype=float)
    pi = reverse_optimize(Sigma, w_mkt, risk_aversion)

    if P is None or Q is None or P.size == 0:
        mu_post = pi
        sigma_post = Sigma
        view_impact = {"n_views": 0}
    else:
        P = np.asarray(P, dtype=float)
        Q = np.asarray(Q, dtype=float).flatten()
        tau_sigma = tau * Sigma
        if omega is None:
            omega = np.diag(np.diag(P @ tau_sigma @ P.T))
        else:
            omega = np.asarray(omega, dtype=float)

        # BL combined mean
        M = np.linalg.inv(np.linalg.inv(tau_sigma) + P.T @ np.linalg.inv(omega) @ P)
        mu_post = M @ (np.linalg.inv(tau_sigma) @ pi + P.T @ np.linalg.inv(omega) @ Q)
        sigma_post = Sigma + M

        view_impact = {
            "n_views": int(len(Q)),
            "shift_from_prior": {
                f"asset_{i}": float(mu_post[i] - pi[i])
                for i in range(len(pi))
            },
        }

    # MV-optimal weights given posterior
    try:
        w_opt = np.linalg.solve(risk_aversion * sigma_post, mu_post)
        # Normalize to sum to 1 (if long-only preferred, clamp here)
        if w_opt.sum() != 0:
            w_opt = w_opt / w_opt.sum()
    except np.linalg.LinAlgError:
        w_opt = w_mkt.copy()

    return BlackLittermanResult(
        mu_posterior=mu_post,
        sigma_posterior=sigma_post,
        weights=w_opt,
        implied_equilibrium=pi,
        view_impact=view_impact,
    )
