"""Parameter optimization for alphas.

Two estimators are provided:

  - GridSearchOptimizer: exhaustive grid over a parameter dict
  - GPOptimizer:         pure-numpy Gaussian process Bayesian optimizer
                          (Matern-5/2 + EI acquisition), no sklearn dependency

Both wrap a walk-forward objective so the metric reported is OOS-honest.
The objective by default is the deflated-Sharpe-penalized OOS Sharpe; you
can pass any callable taking (params) and returning a score.
"""

from shared.optim.objective import WalkForwardObjective
from shared.optim.grid import GridSearchOptimizer
from shared.optim.bayes import GPOptimizer, GPSurrogate

__all__ = [
    "WalkForwardObjective",
    "GridSearchOptimizer",
    "GPOptimizer",
    "GPSurrogate",
]
