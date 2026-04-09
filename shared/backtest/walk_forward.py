"""Walk-forward / out-of-sample validation.

Splits the data into K rolling train/test windows. Each window's test slice
is backtested on the alpha (parameters held fixed; this is OOS validation,
not parameter optimization). The OOS aggregate Sharpe is the headline number.

For parameter optimization, layer a parameter grid on top — that's done in
`scripts/bootstrap/param_search.py` to keep the runner clean.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha
from shared.backtest.metrics import all_metrics
from shared.backtest.runner import BacktestRunner, CostModel


@dataclass
class WalkForwardResult:
    n_windows: int
    window_metrics: list[dict[str, float]]
    oos_aggregate: dict[str, float]
    in_sample_aggregate: dict[str, float]
    consistency_score: float       # 0..1, fraction of windows with positive Sharpe
    sharpe_decay: float            # is_sharpe - oos_sharpe (positive = overfitting)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": "shared.backtest.walk_forward",
            "n_windows": self.n_windows,
            "oos_metrics": self.oos_aggregate,
            "in_sample_metrics": self.in_sample_aggregate,
            "consistency_score": round(self.consistency_score, 4),
            "sharpe_decay": round(self.sharpe_decay, 4),
            "per_window_metrics": self.window_metrics,
        }


def walk_forward(
    alpha: Alpha,
    df: pd.DataFrame,
    n_windows: int = 5,
    train_ratio: float = 0.6,
    cost_model: CostModel | None = None,
    periods_per_year: int = 252 * 24,
) -> WalkForwardResult:
    """Run K rolling-origin windows.

    For each window:
      - train slice: bars [w_start, split)
      - test slice:  bars [split, w_end)
    The alpha runs on the full df slice, but only test-slice returns
    contribute to the OOS metrics. (Since this alpha library doesn't fit
    parameters from data, train slice is informational only.)
    """
    runner = BacktestRunner(
        cost_model=cost_model or CostModel(),
        periods_per_year=periods_per_year,
        n_trials=n_windows,    # multiple-testing penalty
    )

    n = len(df)
    window_size = max(n // n_windows, 50)
    window_results: list[dict[str, float]] = []
    oos_returns_all: list[float] = []
    is_returns_all: list[float] = []
    n_positive = 0

    for w in range(n_windows):
        w_start = w * window_size
        w_end = min((w + 1) * window_size, n)
        if w_end - w_start < 100:
            continue
        split = int(w_start + (w_end - w_start) * train_ratio)
        if split >= w_end - 10:
            continue

        # Run on the full window so the alpha has lookback context
        sub = df.iloc[w_start:w_end]
        report = runner.run(alpha, sub)
        # Slice out IS / OOS portions
        local_split = split - w_start
        is_ret = report.returns.iloc[:local_split]
        oos_ret = report.returns.iloc[local_split:]

        oos_metrics = all_metrics(
            oos_ret.values, periods_per_year=periods_per_year, n_trials=n_windows
        )
        is_metrics = all_metrics(
            is_ret.values, periods_per_year=periods_per_year, n_trials=n_windows
        )
        window_results.append(
            {
                "window": w,
                "is_sharpe": is_metrics["sharpe"],
                "oos_sharpe": oos_metrics["sharpe"],
                "oos_max_dd": oos_metrics["max_drawdown"],
                "oos_n_obs": oos_metrics["n_obs"],
                "oos_profit_factor": oos_metrics["profit_factor"],
            }
        )
        oos_returns_all.extend(oos_ret.values.tolist())
        is_returns_all.extend(is_ret.values.tolist())
        if oos_metrics["sharpe"] > 0:
            n_positive += 1

    if not window_results:
        empty = all_metrics([], periods_per_year=periods_per_year)
        return WalkForwardResult(
            n_windows=0,
            window_metrics=[],
            oos_aggregate=empty,
            in_sample_aggregate=empty,
            consistency_score=0.0,
            sharpe_decay=0.0,
        )

    oos_agg = all_metrics(
        oos_returns_all, periods_per_year=periods_per_year, n_trials=len(window_results)
    )
    is_agg = all_metrics(
        is_returns_all, periods_per_year=periods_per_year, n_trials=len(window_results)
    )

    consistency = n_positive / len(window_results)
    decay = is_agg["sharpe"] - oos_agg["sharpe"]

    return WalkForwardResult(
        n_windows=len(window_results),
        window_metrics=window_results,
        oos_aggregate=oos_agg,
        in_sample_aggregate=is_agg,
        consistency_score=consistency,
        sharpe_decay=decay,
    )
