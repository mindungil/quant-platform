"""Vectorized backtest runner.

Takes an Alpha and an OHLCV DataFrame and produces a `BacktestReport` with
realistic costs (commission + linear + square-root impact slippage),
position-change-driven turnover, and a full metric suite.

Position semantics:
- The alpha emits a position series in [-1, 1], aligned to bar index.
- The runner applies that position from bar `t` to `t+1` (positions are
  already shifted by Alpha.generate to avoid look-ahead).
- Per-bar return = position * pct_change(close) - turnover_cost.

This is bar-resolution, not tick-resolution. For HFT or tight intra-bar
stops use `services/backtest-service/`. This runner is for swing/position
strategies and seed-time validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha
from shared.backtest.metrics import all_metrics


@dataclass
class CostModel:
    commission_bps: float = 4.0          # round-trip per leg, in basis points
    slippage_bps: float = 2.0            # base slippage on every fill
    impact_coef: float = 0.10            # square-root impact: bps per sqrt(participation)
    funding_per_bar_bps: float = 0.0     # cost of carry per bar (perp funding etc.)


@dataclass
class BacktestReport:
    alpha_name: str
    n_bars: int
    metrics: dict[str, float]
    equity_curve: pd.Series
    returns: pd.Series
    positions: pd.Series
    turnover: float
    avg_gross_exposure: float
    cost_model: dict[str, float]
    diagnostics: dict[str, Any] = field(default_factory=dict)
    status: str = "PASSED"
    failure_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict suitable for strategy_registry persistence."""
        return {
            "alpha_name": self.alpha_name,
            "engine": "shared.backtest.runner",
            "n_bars": self.n_bars,
            "status": self.status,
            "failure_reasons": self.failure_reasons,
            "metrics": self.metrics,
            "turnover": round(self.turnover, 4),
            "avg_gross_exposure": round(self.avg_gross_exposure, 4),
            "cost_model": self.cost_model,
            "diagnostics": self.diagnostics,
            # NOTE: equity_curve / returns / positions deliberately omitted
            # from the persisted blob — they can be re-derived and would bloat
            # strategy rows.
        }


# Threshold tiers — different bars for seed-time validation vs. live promotion.
# Seed thresholds raised: a 0.3 Sharpe seed lets through strategies that are
# indistinguishable from noise, wasting shadow capacity. 0.5 minimum ensures
# at least weak edge before consuming shadow resources.
SEED_THRESHOLDS = {
    "sharpe_min": 0.50,
    "sortino_min": 0.60,
    "max_drawdown_max": 0.35,
    "profit_factor_min": 1.10,
    "min_n_obs": 250,
    "deflated_sharpe_pvalue_min": 0.20,
}

LIVE_THRESHOLDS = {
    "sharpe_min": 1.0,
    "sortino_min": 1.2,
    "max_drawdown_max": 0.25,
    "profit_factor_min": 1.05,
    "min_n_obs": 500,
    "deflated_sharpe_pvalue_min": 0.55,
}


@dataclass
class BacktestRunner:
    cost_model: CostModel = field(default_factory=CostModel)
    periods_per_year: int = 252 * 24     # default to hourly bars
    n_trials: int = 1                    # for deflated Sharpe; set to N when sweeping
    pass_thresholds: dict[str, float] = field(default_factory=lambda: dict(LIVE_THRESHOLDS))

    def run(self, alpha: Alpha, df: pd.DataFrame) -> BacktestReport:
        if "close" not in df.columns:
            raise ValueError("df must contain a 'close' column")

        signal = alpha.generate(df)
        positions = signal.position.reindex(df.index).fillna(0.0)

        # Per-bar log/arith returns of the *underlying*
        close = df["close"].astype(float)
        bar_ret = close.pct_change().fillna(0.0)

        # Position-driven returns
        gross_ret = positions * bar_ret

        # Turnover-driven costs: |Δposition| × (commission + base slippage)
        dpos = positions.diff().abs().fillna(positions.iloc[0:1].abs().reindex(positions.index, fill_value=0.0))
        cost_per_unit = (self.cost_model.commission_bps + self.cost_model.slippage_bps) / 1e4

        # Square-root impact: scales with |Δposition|^0.5 (proxy for participation rate)
        impact = self.cost_model.impact_coef * np.sqrt(dpos) / 1e4

        # Funding/carry cost (paid per bar on absolute exposure)
        funding_cost = positions.abs() * (self.cost_model.funding_per_bar_bps / 1e4)

        net_ret = gross_ret - dpos * cost_per_unit - impact - funding_cost
        equity = (1.0 + net_ret).cumprod()

        metrics = all_metrics(
            net_ret.values,
            periods_per_year=self.periods_per_year,
            n_trials=self.n_trials,
        )

        turnover = float(dpos.sum())
        avg_gross = float(positions.abs().mean())

        # Pass/fail
        thr = self.pass_thresholds
        failures: list[str] = []
        if metrics["n_obs"] < thr["min_n_obs"]:
            failures.append(f"insufficient_obs ({metrics['n_obs']} < {thr['min_n_obs']})")
        if metrics["sharpe"] < thr["sharpe_min"]:
            failures.append(f"sharpe_below_min ({metrics['sharpe']:.2f} < {thr['sharpe_min']})")
        if metrics["sortino"] < thr["sortino_min"]:
            failures.append(f"sortino_below_min ({metrics['sortino']:.2f} < {thr['sortino_min']})")
        if metrics["max_drawdown"] > thr["max_drawdown_max"]:
            failures.append(
                f"max_drawdown_above ({metrics['max_drawdown']:.2f} > {thr['max_drawdown_max']})"
            )
        if metrics["profit_factor"] < thr["profit_factor_min"]:
            failures.append(
                f"profit_factor_below_min ({metrics['profit_factor']:.2f} < {thr['profit_factor_min']})"
            )
        if metrics["deflated_sharpe_pvalue"] < thr["deflated_sharpe_pvalue_min"]:
            failures.append(
                f"deflated_sharpe_below ({metrics['deflated_sharpe_pvalue']:.2f} < {thr['deflated_sharpe_pvalue_min']})"
            )

        status = "PASSED" if not failures else "FAILED"

        return BacktestReport(
            alpha_name=alpha.name,
            n_bars=len(df),
            metrics=metrics,
            equity_curve=equity,
            returns=net_ret,
            positions=positions,
            turnover=turnover,
            avg_gross_exposure=avg_gross,
            cost_model={
                "commission_bps": self.cost_model.commission_bps,
                "slippage_bps": self.cost_model.slippage_bps,
                "impact_coef": self.cost_model.impact_coef,
                "funding_per_bar_bps": self.cost_model.funding_per_bar_bps,
            },
            diagnostics=signal.diagnostics,
            status=status,
            failure_reasons=failures,
        )


def run_backtest(
    alpha: Alpha,
    df: pd.DataFrame,
    cost_model: CostModel | None = None,
    periods_per_year: int = 252 * 24,
    n_trials: int = 1,
) -> BacktestReport:
    runner = BacktestRunner(
        cost_model=cost_model or CostModel(),
        periods_per_year=periods_per_year,
        n_trials=n_trials,
    )
    return runner.run(alpha, df)
