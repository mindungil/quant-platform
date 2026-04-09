"""Pure-numpy Bayesian optimization with Gaussian process surrogate.

Implements:
  - Matérn 5/2 kernel + RBF kernel options
  - Closed-form GP posterior (mean + variance) via Cholesky
  - Expected Improvement (EI) acquisition function
  - Random restart sampling for acquisition optimization

Why pure numpy:
- The repo doesn't ship sklearn / scikit-optimize / GPyOpt
- The objective evaluations dominate runtime (each is a walk-forward backtest)
  so a hand-rolled GP is plenty fast — we don't need a pro library
- Pure-numpy is easy to test and reason about

Reference: Frazier 2018, "A Tutorial on Bayesian Optimization", §3-4.
Clean-room implementation; no GPL code reused.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np


# ---- kernels ----


def matern52(x1: np.ndarray, x2: np.ndarray, lengthscale: float) -> np.ndarray:
    """Matérn 5/2 kernel: k(r) = (1 + √5 r/l + 5 r²/(3l²)) exp(-√5 r/l)"""
    diff = x1[:, None, :] - x2[None, :, :]
    r = np.sqrt(np.sum((diff / lengthscale) ** 2, axis=-1) + 1e-12)
    sqrt5_r = math.sqrt(5.0) * r
    return (1.0 + sqrt5_r + (5.0 / 3.0) * r * r) * np.exp(-sqrt5_r)


def rbf(x1: np.ndarray, x2: np.ndarray, lengthscale: float) -> np.ndarray:
    diff = x1[:, None, :] - x2[None, :, :]
    sq = np.sum((diff / lengthscale) ** 2, axis=-1)
    return np.exp(-0.5 * sq)


# ---- GP surrogate ----


@dataclass
class GPSurrogate:
    """Zero-mean GP with fixed kernel hyperparameters.

    For our use case, we don't need to MLE the kernel — we just want a
    smooth function approximator that supports posterior queries. Default
    lengthscale of 0.3 works for unit-cube inputs.
    """
    lengthscale: float = 0.3
    noise: float = 1e-4
    kernel: str = "matern52"

    def __post_init__(self) -> None:
        self.X_: np.ndarray | None = None
        self.y_: np.ndarray | None = None
        self.L_: np.ndarray | None = None
        self.alpha_: np.ndarray | None = None

    def _kfn(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        if self.kernel == "rbf":
            return rbf(a, b, self.lengthscale)
        return matern52(a, b, self.lengthscale)

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        X = np.atleast_2d(X).astype(float)
        y = np.asarray(y, dtype=float)
        n = X.shape[0]
        K = self._kfn(X, X) + self.noise * np.eye(n)
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            # Add jitter on near-singular kernel
            K += 1e-2 * np.eye(n)
            L = np.linalg.cholesky(K)
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
        self.X_ = X
        self.y_ = y
        self.L_ = L
        self.alpha_ = alpha

    def predict(self, X_new: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.X_ is None:
            return np.zeros(len(X_new)), np.ones(len(X_new))
        X_new = np.atleast_2d(X_new).astype(float)
        Ks = self._kfn(X_new, self.X_)
        mean = Ks @ self.alpha_
        v = np.linalg.solve(self.L_, Ks.T)
        Kss_diag = np.diag(self._kfn(X_new, X_new))
        var = np.maximum(Kss_diag - np.sum(v * v, axis=0), 1e-9)
        return mean, var


# ---- acquisition ----


def expected_improvement(
    mean: np.ndarray,
    var: np.ndarray,
    y_best: float,
    xi: float = 0.01,
) -> np.ndarray:
    """Closed-form EI for a maximization problem."""
    std = np.sqrt(var)
    improvement = mean - y_best - xi
    z = np.where(std > 1e-9, improvement / np.where(std > 1e-9, std, 1.0), 0.0)
    # Standard normal CDF/PDF (no scipy)
    cdf = 0.5 * (1.0 + _erf(z / math.sqrt(2.0)))
    pdf = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    ei = improvement * cdf + std * pdf
    return np.where(std > 1e-9, ei, 0.0)


def _erf(x: np.ndarray) -> np.ndarray:
    # Abramowitz & Stegun 7.1.26 — accurate to ~1.5e-7
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-ax * ax)
    return sign * y


# ---- optimizer ----


@dataclass
class GPOptimizer:
    """BO loop over a continuous parameter space.

    `space` maps param name -> (low, high) tuple. Discrete params can be
    encoded by rounding inside the alpha factory.

    Example:
        space = {"adx_min": (5.0, 25.0), "donchian_window": (20, 100)}
    """
    objective: Callable[[dict[str, Any]], float]
    space: dict[str, tuple[float, float]]
    n_initial: int = 6
    n_iter: int = 20
    seed: int = 0
    surrogate: GPSurrogate = field(default_factory=GPSurrogate)
    history: list[tuple[dict[str, Any], float]] = field(default_factory=list)

    def fit(self) -> tuple[dict[str, Any], float]:
        rng = np.random.default_rng(self.seed)
        keys = list(self.space.keys())
        bounds = np.array([self.space[k] for k in keys], dtype=float)
        n_dim = len(keys)

        def to_unit(p: np.ndarray) -> np.ndarray:
            return (p - bounds[:, 0]) / (bounds[:, 1] - bounds[:, 0])

        def from_unit(u: np.ndarray) -> np.ndarray:
            return bounds[:, 0] + u * (bounds[:, 1] - bounds[:, 0])

        # ---- initial random sample ----
        X_unit = rng.uniform(0.0, 1.0, size=(self.n_initial, n_dim))
        X_real = np.array([from_unit(u) for u in X_unit])
        y = []
        for x in X_real:
            params = {k: float(x[i]) for i, k in enumerate(keys)}
            score = self._safe_eval(params)
            y.append(score)
            self.history.append((params, score))
        y = np.array(y, dtype=float)

        # ---- BO loop ----
        for _ in range(self.n_iter):
            self.surrogate.fit(X_unit, y)
            y_best = float(np.max(y))

            # Sample candidates and pick the EI-maximizer
            n_cand = 1024
            cand = rng.uniform(0.0, 1.0, size=(n_cand, n_dim))
            mean, var = self.surrogate.predict(cand)
            ei = expected_improvement(mean, var, y_best)
            best_idx = int(np.argmax(ei))
            x_next = cand[best_idx]

            x_real = from_unit(x_next)
            params = {k: float(x_real[i]) for i, k in enumerate(keys)}
            score = self._safe_eval(params)
            self.history.append((params, score))

            X_unit = np.vstack([X_unit, x_next[None, :]])
            y = np.append(y, score)

        best_idx = int(np.argmax(y))
        best_x = from_unit(X_unit[best_idx])
        best_params = {k: float(best_x[i]) for i, k in enumerate(keys)}
        return best_params, float(y[best_idx])

    def _safe_eval(self, params: dict[str, Any]) -> float:
        try:
            return float(self.objective(params))
        except Exception:
            return -1e9
