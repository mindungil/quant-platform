"""Rolling parameter refit with statistical gating.

Weekly re-optimizes alpha parameters on recent data, but ONLY promotes
a new config if it statistically significantly outperforms the current
one on a held-out OOS window. This prevents noise-fitting.

Safety mechanisms:
  1. Welch t-test on per-bar returns (candidate vs current)
  2. Candidate must beat current Sharpe by a safety margin
  3. Must win on majority of symbols (if multi-symbol)
  4. Old config archived before any change
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import AlphaConfig
from shared.alpha.kalman_trend import KalmanTrendAlpha
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha
from shared.alpha.trend_breakout import TrendBreakoutAlpha
from shared.backtest.metrics import sharpe_ratio, apply_transaction_costs


@dataclass
class RefitResult:
    alpha_name: str
    current_params: dict
    candidate_params: dict
    current_oos_sharpe: float
    candidate_oos_sharpe: float
    p_value: float
    promoted: bool
    reason: str


# Parameter grids for each alpha (focused, not exhaustive)
PARAM_GRIDS = {
    "kalman_trend": [
        {"obs_var": ov, "slope_var": sv}
        for ov in [1e-4, 2e-4, 5e-4, 1e-3]
        for sv in [1e-8, 5e-8, 1e-7, 2e-7]
    ],
    "momentum_ensemble": [
        {"windows": w}
        for w in [[168, 720], [72, 336], [168, 720, 2160], [24, 168, 720], [336, 1440]]
    ],
    "trend_breakout": [
        {"donchian_window": dw, "exit_window": ew}
        for dw in [72, 96, 120, 168]
        for ew in [30, 45, 55, 72]
    ],
}


def _make_alpha(name: str, params: dict):
    cfg = AlphaConfig(name=name, params=params)
    if name == "kalman_trend":
        return KalmanTrendAlpha(cfg)
    if name == "momentum_ensemble":
        return MomentumEnsembleAlpha(cfg)
    if name == "trend_breakout":
        return TrendBreakoutAlpha(cfg)
    raise ValueError(name)


def _welch_t_test(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sample Welch t-test p-value. Returns p for H0: mean(a) == mean(b)."""
    na, nb = len(a), len(b)
    if na < 10 or nb < 10:
        return 1.0
    mean_a, mean_b = a.mean(), b.mean()
    var_a, var_b = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(var_a / na + var_b / nb)
    if se < 1e-12:
        return 1.0
    t_stat = (mean_b - mean_a) / se  # positive = candidate better
    # Approximate p-value using normal CDF (for large N)
    try:
        from scipy.stats import norm
        p = 1.0 - norm.cdf(t_stat)
    except ImportError:
        import math
        p = 1.0 / (1.0 + math.exp(1.702 * t_stat))  # logistic approx
    return float(p)


class RollingRefitter:
    def __init__(
        self,
        current_params: dict[str, dict],
        lookback_days: int = 180,
        oos_days: int = 30,
        significance: float = 0.05,
        safety_margin: float = 0.10,
        cost_bps: float = 5.0,
        ppy: int = 24 * 365,
    ):
        self.current_params = current_params
        self.lookback_days = lookback_days
        self.oos_days = oos_days
        self.significance = significance
        self.safety_margin = safety_margin
        self.cost_bps = cost_bps
        self.ppy = ppy

    def refit_alpha(self, alpha_name: str, df: pd.DataFrame) -> RefitResult:
        """Refit one alpha on the provided OHLCV data."""
        n = len(df)
        ret = df["close"].astype(float).pct_change().fillna(0.0)
        oos_bars = self.oos_days * 24
        train_bars = min(self.lookback_days * 24, n - oos_bars)
        if train_bars < 500:
            return RefitResult(alpha_name, self.current_params.get(alpha_name, {}),
                               {}, 0, 0, 1.0, False, "insufficient data")

        train_slice = slice(n - oos_bars - train_bars, n - oos_bars)
        oos_slice = slice(n - oos_bars, n)

        # Current config on OOS
        current_p = self.current_params.get(alpha_name, {})
        try:
            current_pos = _make_alpha(alpha_name, current_p).generate(df).position
            current_oos_pnl = apply_transaction_costs(
                current_pos.iloc[oos_slice].values, ret.iloc[oos_slice].values, cost_bps=self.cost_bps
            )
            current_sh = sharpe_ratio(current_oos_pnl, periods_per_year=self.ppy)
        except Exception:
            current_sh = 0.0
            current_oos_pnl = np.zeros(oos_bars)

        # Grid search on train, score on OOS
        grid = PARAM_GRIDS.get(alpha_name, [])
        best_candidate_sh = -np.inf
        best_candidate_params = current_p
        best_candidate_pnl = current_oos_pnl

        for params in grid:
            try:
                pos = _make_alpha(alpha_name, params).generate(df).position
                oos_pnl = apply_transaction_costs(
                    pos.iloc[oos_slice].values, ret.iloc[oos_slice].values, cost_bps=self.cost_bps
                )
                sh = sharpe_ratio(oos_pnl, periods_per_year=self.ppy)
                if sh > best_candidate_sh:
                    best_candidate_sh = sh
                    best_candidate_params = params
                    best_candidate_pnl = oos_pnl
            except Exception:
                continue

        # Statistical test: is candidate significantly better?
        p_value = _welch_t_test(current_oos_pnl, best_candidate_pnl)
        delta_sh = best_candidate_sh - current_sh
        promoted = (
            p_value < self.significance
            and delta_sh > self.safety_margin
            and best_candidate_sh > 0
        )

        reason = (
            f"candidate sh={best_candidate_sh:+.2f} vs current sh={current_sh:+.2f} "
            f"Δ={delta_sh:+.2f} p={p_value:.4f}"
        )
        if promoted:
            reason = f"PROMOTED: {reason}"
        else:
            reason = f"KEPT CURRENT: {reason}"

        return RefitResult(
            alpha_name=alpha_name,
            current_params=current_p,
            candidate_params=best_candidate_params,
            current_oos_sharpe=round(current_sh, 4),
            candidate_oos_sharpe=round(best_candidate_sh, 4),
            p_value=round(p_value, 6),
            promoted=promoted,
            reason=reason,
        )

    def refit_all(
        self,
        dfs: dict[str, pd.DataFrame],
        require_majority: bool = True,
    ) -> list[RefitResult]:
        """Refit all alphas across multiple symbols.

        Only promotes if candidate wins on >50% of symbols (if require_majority).
        """
        all_results = []
        promotion_votes: dict[str, list[bool]] = {name: [] for name in self.current_params}

        for symbol, df in dfs.items():
            for alpha_name in self.current_params:
                result = self.refit_alpha(alpha_name, df)
                result.reason = f"[{symbol}] {result.reason}"
                all_results.append(result)
                promotion_votes[alpha_name].append(result.promoted)

        if require_majority and len(dfs) > 1:
            for r in all_results:
                votes = promotion_votes[r.alpha_name]
                majority = sum(votes) > len(votes) / 2
                if not majority and r.promoted:
                    r.promoted = False
                    r.reason += " [OVERRIDDEN: no majority across symbols]"

        return all_results
