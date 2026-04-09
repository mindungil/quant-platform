"""Walk-forward objective wrapper for parameter optimization.

Given an alpha factory (callable that builds an Alpha from a params dict)
and a dataframe, runs walk-forward backtest and returns a single score.
The default score is OOS Sharpe scaled by deflated-Sharpe p-value:

    score = oos_sharpe * deflated_sharpe_pvalue

This naturally penalizes lucky-high-Sharpe strategies that aren't
statistically significant after multiple-testing correction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from shared.alpha.base import Alpha
from shared.backtest.runner import CostModel
from shared.backtest.walk_forward import walk_forward


@dataclass
class WalkForwardObjective:
    alpha_factory: Callable[[dict[str, Any]], Alpha]
    df: pd.DataFrame
    n_windows: int = 4
    train_ratio: float = 0.6
    cost_model: CostModel | None = None
    periods_per_year: int = 252 * 24
    score_fn: Callable[[dict[str, float]], float] | None = None

    def __call__(self, params: dict[str, Any]) -> float:
        try:
            alpha = self.alpha_factory(params)
            res = walk_forward(
                alpha,
                self.df,
                n_windows=self.n_windows,
                train_ratio=self.train_ratio,
                cost_model=self.cost_model,
                periods_per_year=self.periods_per_year,
            )
        except Exception:
            return -1e9
        oos = res.oos_aggregate
        if self.score_fn is not None:
            return float(self.score_fn(oos))
        # Default score: OOS Sharpe × DSR p-value × consistency
        sharpe = float(oos.get("sharpe", 0.0))
        dsr = float(oos.get("deflated_sharpe_pvalue", 0.0))
        consistency = float(res.consistency_score)
        return sharpe * max(dsr, 0.05) * max(consistency, 0.1)
