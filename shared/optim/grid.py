"""Exhaustive grid search optimizer."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class GridSearchOptimizer:
    objective: Callable[[dict[str, Any]], float]
    grid: dict[str, list[Any]]
    history: list[tuple[dict[str, Any], float]] = field(default_factory=list)

    def fit(self) -> tuple[dict[str, Any], float]:
        keys = list(self.grid.keys())
        values = [self.grid[k] for k in keys]

        best_params: dict[str, Any] = {}
        best_score = -float("inf")

        for combo in itertools.product(*values):
            params = dict(zip(keys, combo))
            score = self.objective(params)
            self.history.append((params, score))
            if score > best_score:
                best_score = score
                best_params = params
        return best_params, best_score
