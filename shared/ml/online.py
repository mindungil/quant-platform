"""Online learning primitives — continuous adaptation without GPU.

Why online learning matters here:
- A bagged forest retrained every 720 bars adapts slowly to regime shifts.
- An ONLINE model updates its weights on EVERY incoming bar in O(d²) time.
- With exponential forgetting (λ < 1), recent bars get more weight, so the
  model effectively forgets stale market behavior automatically.
- Pure CPU, pure numpy, fits inside microsecond budgets — no GPU needed.

Two estimators:
  1. OnlineRidge — closed-form ridge with running normal equations.
  2. RecursiveLeastSquares (RLS) — Sherman-Morrison rank-1 update with
     exponential forgetting factor λ. Numerically stable and well-known
     in adaptive filtering literature (Haykin, Adaptive Filter Theory).

Both expose `.update(x, y)` and `.predict(x)` so they can run inside the
backtest's bar loop.

Reference: Haykin, "Adaptive Filter Theory" 4th ed, Ch. 14.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class OnlineRidge:
    """Closed-form online ridge with running normal equations.

    Maintains A = X'X + αI, b = X'y. To predict, solve A w = b.
    Solving every bar is O(d³); for small d (~20) this is fine.
    """

    n_features: int
    alpha: float = 1.0
    _A: np.ndarray = field(init=False, repr=False)
    _b: np.ndarray = field(init=False, repr=False)
    _w: np.ndarray | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        d = self.n_features
        self._A = self.alpha * np.eye(d)
        self._b = np.zeros(d)
        self._w = None

    def update(self, x: np.ndarray, y: float) -> None:
        x = np.asarray(x, dtype=float).reshape(-1)
        self._A += np.outer(x, x)
        self._b += y * x
        self._w = None

    def fit_batch(self, X: np.ndarray, y: np.ndarray) -> None:
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self._A = self.alpha * np.eye(self.n_features) + X.T @ X
        self._b = X.T @ y
        self._w = None

    def _solve(self) -> np.ndarray:
        try:
            self._w = np.linalg.solve(self._A, self._b)
        except np.linalg.LinAlgError:
            self._w = np.zeros(self.n_features)
        return self._w

    def predict(self, x: np.ndarray) -> float:
        if self._w is None:
            self._solve()
        x = np.asarray(x, dtype=float).reshape(-1)
        return float(x @ self._w)


@dataclass
class RecursiveLeastSquares:
    """RLS with exponential forgetting.

    Maintains P (covariance proxy), w (weights). Per-step O(d²) via
    Sherman-Morrison. λ ∈ (0, 1] is the forgetting factor; smaller λ
    means faster adaptation, more variance.

    Update equations (per bar):
        k = (P x) / (λ + x' P x)
        e = y - w' x
        w ← w + k e
        P ← (P - k x' P) / λ
    """

    n_features: int
    forgetting: float = 0.995
    init_var: float = 1000.0
    _P: np.ndarray = field(init=False, repr=False)
    _w: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        d = self.n_features
        self._P = self.init_var * np.eye(d)
        self._w = np.zeros(d)

    def update(self, x: np.ndarray, y: float) -> None:
        x = np.asarray(x, dtype=float).reshape(-1)
        Px = self._P @ x
        denom = self.forgetting + float(x @ Px)
        if abs(denom) < 1e-12:
            return
        k = Px / denom
        err = y - float(x @ self._w)
        self._w = self._w + k * err
        self._P = (self._P - np.outer(k, Px)) / self.forgetting

    def predict(self, x: np.ndarray) -> float:
        x = np.asarray(x, dtype=float).reshape(-1)
        return float(x @ self._w)

    @property
    def weights(self) -> np.ndarray:
        return self._w.copy()
