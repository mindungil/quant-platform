"""Performance attribution for multi-strategy portfolios.

Given an ensemble result and the underlying returns, decomposes total
portfolio PnL into per-alpha contributions:

  contribution_i(t) = w_i(t) * pos_i(t) * ret(t)

Reports per-alpha:
  - cumulative contribution
  - Sharpe of contribution
  - average exposure
  - turnover
  - hit ratio (fraction of bars with positive contribution)

Plus a Brinson-style decomposition:
  total_pnl = sum_i contribution_i  (no interaction term — strategies
              already operate on the same underlying so there's nothing
              to attribute to "selection")

This is the "which alpha actually made me money" report. Useful for
deciding which alphas to deactivate when capital is tight.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from shared.backtest.metrics import all_metrics
from shared.portfolio.ensemble import EnsembleResult


@dataclass
class AttributionReport:
    per_alpha: pd.DataFrame
    total_metrics: dict[str, float]
    diversification_ratio: float
    n_alphas: int

    def to_dict(self) -> dict:
        return {
            "per_alpha": self.per_alpha.reset_index().to_dict(orient="records"),
            "total_metrics": self.total_metrics,
            "diversification_ratio": round(self.diversification_ratio, 4),
            "n_alphas": self.n_alphas,
        }


def attribute(
    ensemble: EnsembleResult,
    alpha_positions: dict[str, pd.Series],
    underlying_returns: pd.Series,
    periods_per_year: int = 252 * 24,
) -> AttributionReport:
    """Decompose ensemble PnL into per-alpha contributions.

    The ensemble's `alpha_weights` already says how much weight each alpha
    received per bar. The contribution of alpha `i` at bar `t` is:

        c_i(t) = alpha_weights_i(t) * alpha_positions_i(t) * ret(t)

    Sum over `i` reproduces ensemble.target_position * ret EXCEPT for
    vol-targeting and tail-hedge scaling, which act on the combined
    position. We compute the residual ("hedge_overlay") so the row totals
    match exactly.
    """
    weights = ensemble.alpha_weights
    if weights.empty or not alpha_positions:
        return AttributionReport(
            per_alpha=pd.DataFrame(),
            total_metrics={},
            diversification_ratio=0.0,
            n_alphas=0,
        )

    pos_df = pd.DataFrame(alpha_positions).reindex(weights.index).fillna(0.0)
    ret = underlying_returns.reindex(weights.index).fillna(0.0)

    contributions = pd.DataFrame(0.0, index=weights.index, columns=weights.columns)
    for col in weights.columns:
        contributions[col] = weights[col] * pos_df[col] * ret

    # Sum-of-pieces vs realized — the residual is the vol-targeting / hedge effect
    realized = ensemble.target_position * ret
    residual = realized - contributions.sum(axis=1)
    contributions["_overlay"] = residual

    # Per-alpha summary
    rows = []
    for col in contributions.columns:
        c = contributions[col]
        cum = float(c.sum())
        m = all_metrics(c.values, periods_per_year=periods_per_year)
        avg_exposure = float(weights[col].mean()) if col in weights.columns else 0.0
        turnover = float(weights[col].diff().abs().sum()) if col in weights.columns else 0.0
        hit_ratio = float((c > 0).mean())
        rows.append(
            {
                "alpha": col,
                "cumulative_pnl": round(cum, 6),
                "sharpe": m["sharpe"],
                "max_drawdown": m["max_drawdown"],
                "avg_weight": round(avg_exposure, 4),
                "weight_turnover": round(turnover, 4),
                "hit_ratio": round(hit_ratio, 4),
            }
        )

    per_alpha_df = pd.DataFrame(rows).set_index("alpha")

    total_m = all_metrics(realized.values, periods_per_year=periods_per_year)

    # Diversification ratio: sum of marginal vols / portfolio vol
    # Higher = better diversification (less concentration risk)
    component_vols = contributions.drop(columns=["_overlay"]).std(ddof=0).abs().sum()
    portfolio_vol = realized.std(ddof=0)
    div_ratio = float(component_vols / portfolio_vol) if portfolio_vol > 1e-9 else 0.0

    return AttributionReport(
        per_alpha=per_alpha_df,
        total_metrics=total_m,
        diversification_ratio=div_ratio,
        n_alphas=int(weights.shape[1]),
    )
