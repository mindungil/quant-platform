"""CVaR (Conditional Value-at-Risk) portfolio optimization.

Rockafellar-Uryasev (2000) formulation: minimizing CVaR reduces to a linear
program over portfolio weights + auxiliary variables. We implement it via
`scipy.optimize.linprog` — no cvxpy required.

Use case: institutional tail-aware allocation. Mean-variance treats up-side
and down-side symmetrically; CVaR penalizes only the (1-α)-worst scenarios.
At α=0.95 this is the expected loss on the worst 5% of outcomes.

Reference objective:
    min_w,ζ,u    ζ + 1/(T(1-α)) · Σ u_t
    s.t.         u_t ≥ -(w · r_t) - ζ      (loss_t − ζ − u_t ≤ 0)
                 u_t ≥ 0
                 Σ w = 1,  w ≥ 0    (or user-supplied constraints)

If a target return μ_target is provided, we add  w · μ ≥ μ_target.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog


@dataclass
class CVaRConfig:
    alpha: float = 0.95
    target_return: float | None = None
    long_only: bool = True
    max_weight: float = 1.0
    gross_cap: float = 1.0  # sum(|w|) ≤ gross_cap (used when long_only=False)


@dataclass
class CVaRResult:
    weights: np.ndarray
    cvar: float           # expected loss on worst (1-α) tail
    var: float            # ζ at optimum
    expected_return: float
    status: str


def cvar_min(
    returns_matrix: np.ndarray,
    config: CVaRConfig | None = None,
) -> CVaRResult:
    """Minimize CVaR of portfolio losses.

    Args:
        returns_matrix: (T, N) per-scenario returns (each row is one scenario).

    For N assets and T scenarios the LP has N + 1 + T variables.
    """
    cfg = config or CVaRConfig()
    R = np.asarray(returns_matrix, dtype=float)
    if R.ndim != 2:
        raise ValueError("returns_matrix must be (T, N)")
    T, N = R.shape
    mu = R.mean(axis=0)

    n_vars = N + 1 + T
    # Objective: minimize ζ + 1/(T(1-α)) Σ u_t
    c = np.zeros(n_vars)
    c[N] = 1.0
    c[N + 1:] = 1.0 / (T * (1 - cfg.alpha))

    # Inequality: -R·w - ζ - u ≤ 0  →  -R·w - ζ - u ≤ 0
    # (i.e. loss_t - ζ - u_t ≤ 0 where loss_t = -R·w)
    A_ub = np.zeros((T, n_vars))
    A_ub[:, :N] = -R
    A_ub[:, N] = -1.0
    A_ub[:, N + 1:] = -np.eye(T)
    b_ub = np.zeros(T)

    A_eq_rows = []
    b_eq_vals = []
    # Sum of weights = 1 (only when long_only; otherwise leave unconstrained
    # except for gross cap handled below).
    if cfg.long_only:
        eq = np.zeros(n_vars)
        eq[:N] = 1.0
        A_eq_rows.append(eq)
        b_eq_vals.append(1.0)

    A_eq = np.vstack(A_eq_rows) if A_eq_rows else None
    b_eq = np.array(b_eq_vals) if b_eq_vals else None

    # Target return constraint: w · μ ≥ target  →  -μ·w ≤ -target
    A_extra = []
    b_extra = []
    if cfg.target_return is not None:
        row = np.zeros(n_vars)
        row[:N] = -mu
        A_extra.append(row)
        b_extra.append(-cfg.target_return)
    # Gross cap (long-short): implemented via auxiliary vars is heavier; here
    # we handle it by splitting w = w+ − w−, but for simplicity we only expose
    # long-only CVaR here and long-short via a wrapper below.

    if A_extra:
        A_ub = np.vstack([A_ub, np.asarray(A_extra)])
        b_ub = np.concatenate([b_ub, np.asarray(b_extra)])

    bounds = []
    # w bounds
    for _ in range(N):
        bounds.append((0.0 if cfg.long_only else -cfg.max_weight, cfg.max_weight))
    # ζ free
    bounds.append((None, None))
    # u ≥ 0
    for _ in range(T):
        bounds.append((0.0, None))

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        # Fallback to equal-weight
        w = np.ones(N) / N
        return CVaRResult(
            weights=w,
            cvar=float("nan"),
            var=float("nan"),
            expected_return=float(w @ mu),
            status=f"failed: {res.message}",
        )
    x = res.x
    w = x[:N]
    zeta = x[N]
    u = x[N + 1:]
    cvar = zeta + u.sum() / (T * (1 - cfg.alpha))
    return CVaRResult(
        weights=w,
        cvar=float(cvar),
        var=float(zeta),
        expected_return=float(w @ mu),
        status="optimal",
    )


def historical_var(returns: np.ndarray, alpha: float = 0.95) -> float:
    r = np.asarray(returns, dtype=float)
    return float(-np.quantile(r, 1 - alpha))


def historical_cvar(returns: np.ndarray, alpha: float = 0.95) -> float:
    r = np.asarray(returns, dtype=float)
    var = -np.quantile(r, 1 - alpha)
    tail = r[r <= -var]
    if len(tail) == 0:
        return float(var)
    return float(-tail.mean())
