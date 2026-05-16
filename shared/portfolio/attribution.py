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


@dataclass
class RegimeAttributionReport:
    """Per-(alpha, regime) PnL matrix.

    `cumulative_by_regime` is a DataFrame (alpha × regime_label) of total
    contribution in each regime. `sharpe_by_regime` mirrors that with the
    Sharpe of each (alpha, regime) slice."""

    cumulative_by_regime: pd.DataFrame
    sharpe_by_regime: pd.DataFrame
    samples_by_regime: pd.DataFrame
    n_regimes: int
    n_alphas: int

    def to_dict(self) -> dict:
        return {
            "cumulative_by_regime": self.cumulative_by_regime.to_dict(orient="index"),
            "sharpe_by_regime": self.sharpe_by_regime.to_dict(orient="index"),
            "samples_by_regime": self.samples_by_regime.to_dict(orient="index"),
            "n_regimes": self.n_regimes,
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


# ──────────────────────────────────────────────────────────────────
# V3 #2 additions — regime attribution, dead-alpha flag, rolling
# ──────────────────────────────────────────────────────────────────


def attribute_by_regime(
    ensemble: EnsembleResult,
    alpha_positions: dict[str, pd.Series],
    underlying_returns: pd.Series,
    regime: pd.Series,
    periods_per_year: int = 252 * 24,
) -> RegimeAttributionReport:
    """Slice the per-bar contributions by regime label.

    Answers "which alpha makes money in TREND_UP vs RANGE vs CRISIS?" —
    direct input for sizing decisions and for setting per-regime alpha
    affinity in `shared/regime/__init__.py::DEFAULT_AFFINITY`.
    """
    weights = ensemble.alpha_weights
    if weights.empty or not alpha_positions:
        return RegimeAttributionReport(
            cumulative_by_regime=pd.DataFrame(),
            sharpe_by_regime=pd.DataFrame(),
            samples_by_regime=pd.DataFrame(),
            n_regimes=0,
            n_alphas=0,
        )

    idx = weights.index
    pos_df = pd.DataFrame(alpha_positions).reindex(idx).fillna(0.0)
    ret = underlying_returns.reindex(idx).fillna(0.0)
    regime_aligned = regime.reindex(idx).fillna("unknown").astype(str)

    # Per-bar per-alpha contribution
    contributions: dict[str, pd.Series] = {}
    for col in weights.columns:
        contributions[col] = (weights[col] * pos_df[col] * ret).fillna(0.0)
    contrib_df = pd.DataFrame(contributions)

    regimes = sorted(regime_aligned.unique())
    cum = pd.DataFrame(0.0, index=weights.columns, columns=regimes)
    sharpe = pd.DataFrame(0.0, index=weights.columns, columns=regimes)
    samples = pd.DataFrame(0, index=weights.columns, columns=regimes)

    for r_label in regimes:
        mask = regime_aligned == r_label
        slice_df = contrib_df[mask]
        n = int(mask.sum())
        if n == 0:
            continue
        for col in weights.columns:
            s = slice_df[col]
            cum.loc[col, r_label] = float(s.sum())
            std = float(s.std(ddof=1)) if len(s) > 1 else 0.0
            mean = float(s.mean())
            sharpe.loc[col, r_label] = (
                (mean / std) * np.sqrt(periods_per_year) if std > 1e-12 else 0.0
            )
            samples.loc[col, r_label] = n

    return RegimeAttributionReport(
        cumulative_by_regime=cum,
        sharpe_by_regime=sharpe,
        samples_by_regime=samples,
        n_regimes=len(regimes),
        n_alphas=int(weights.shape[1]),
    )


def flag_dead_alphas(
    per_alpha_df: pd.DataFrame,
    *,
    sharpe_threshold: float = 0.0,
    require_negative_pnl: bool = True,
    min_hit_ratio: float = 0.40,
) -> list[str]:
    """Return alpha names that should be considered for removal.

    Criteria (an alpha is flagged if ALL hold):
      • Sharpe ≤ sharpe_threshold
      • cumulative_pnl ≤ 0 (when require_negative_pnl=True)
      • hit_ratio < min_hit_ratio

    Inputs come from AttributionReport.per_alpha. Excludes the synthetic
    '_overlay' row from the result.
    """
    if per_alpha_df.empty:
        return []
    df = per_alpha_df[per_alpha_df.index != "_overlay"]
    cond = df["sharpe"] <= sharpe_threshold
    if require_negative_pnl:
        cond &= df["cumulative_pnl"] <= 0
    cond &= df["hit_ratio"] < min_hit_ratio
    return list(df.index[cond])


def rolling_attribution_sharpe(
    ensemble: EnsembleResult,
    alpha_positions: dict[str, pd.Series],
    underlying_returns: pd.Series,
    *,
    window: int = 24 * 90,           # 90 days at 1h
    periods_per_year: int = 252 * 24,
) -> pd.DataFrame:
    """Rolling per-alpha Sharpe (annualized) over the last `window` bars.

    Returns a DataFrame (timestamps × alphas) — for charting decay over
    time and for the AlphaPauseDecider input.
    """
    weights = ensemble.alpha_weights
    if weights.empty or not alpha_positions:
        return pd.DataFrame()
    idx = weights.index
    pos_df = pd.DataFrame(alpha_positions).reindex(idx).fillna(0.0)
    ret = underlying_returns.reindex(idx).fillna(0.0)
    out = pd.DataFrame(index=idx, columns=weights.columns, dtype=float)
    annualizer = np.sqrt(periods_per_year)
    for col in weights.columns:
        c = (weights[col] * pos_df[col] * ret).fillna(0.0)
        rmean = c.rolling(window, min_periods=window // 4).mean()
        rstd = c.rolling(window, min_periods=window // 4).std(ddof=0)
        out[col] = (rmean / rstd.replace(0, np.nan)) * annualizer
    return out.fillna(0.0)
