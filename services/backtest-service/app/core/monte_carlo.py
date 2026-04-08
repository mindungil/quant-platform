"""Monte Carlo Simulation — backtest robustness verification.

Runs N simulations by resampling trade returns to estimate the probability
distribution of strategy performance metrics.
"""
import numpy as np
import logging
from typing import Optional

logger = logging.getLogger("backtest-service")


def run_monte_carlo(
    trade_returns: list[float],
    n_simulations: int = 1000,
    confidence_level: float = 0.95,
) -> dict:
    """Run Monte Carlo simulation on trade returns.

    Args:
        trade_returns: List of per-trade percentage returns
        n_simulations: Number of bootstrap simulations
        confidence_level: Confidence level for intervals (default 95%)

    Returns:
        {
            "simulations": n_simulations,
            "confidence_level": confidence_level,
            "sharpe": {"mean": float, "ci_lower": float, "ci_upper": float, "median": float},
            "max_drawdown": {"mean": float, "ci_lower": float, "ci_upper": float, "median": float},
            "total_return": {"mean": float, "ci_lower": float, "ci_upper": float, "median": float},
            "win_rate": {"mean": float, "ci_lower": float, "ci_upper": float, "median": float},
            "profit_factor": {"mean": float, "ci_lower": float, "ci_upper": float, "median": float},
            "robust": bool,  # True if CI lower bound meets criteria
        }
    """
    if len(trade_returns) < 10:
        return {"error": "insufficient_data", "min_trades": 10, "actual": len(trade_returns)}

    returns = np.array(trade_returns)
    alpha = 1 - confidence_level

    # Storage for simulation results
    sharpes = []
    max_drawdowns = []
    total_returns = []
    win_rates = []
    profit_factors = []

    for _ in range(n_simulations):
        # Bootstrap resample (sample with replacement)
        sample = np.random.choice(returns, size=len(returns), replace=True)

        # Sharpe ratio (annualized assuming daily trades)
        mean_r = np.mean(sample)
        std_r = np.std(sample, ddof=1)
        sharpe = (mean_r / std_r) * np.sqrt(252) if std_r > 0 else 0
        sharpes.append(sharpe)

        # Max drawdown from cumulative returns
        cum = np.cumprod(1 + sample / 100)  # convert % to multiplier
        peak = np.maximum.accumulate(cum)
        drawdown = (cum - peak) / peak
        mdd = abs(np.min(drawdown)) if len(drawdown) > 0 else 0
        max_drawdowns.append(mdd)

        # Total return
        total_ret = (cum[-1] - 1) * 100 if len(cum) > 0 else 0
        total_returns.append(total_ret)

        # Win rate
        wins = np.sum(sample > 0)
        wr = wins / len(sample) if len(sample) > 0 else 0
        win_rates.append(wr)

        # Profit factor
        gross_profit = np.sum(sample[sample > 0])
        gross_loss = abs(np.sum(sample[sample < 0]))
        pf = gross_profit / gross_loss if gross_loss > 0 else 10.0
        profit_factors.append(pf)

    def ci(values):
        arr = np.array(values)
        lower = np.percentile(arr, alpha / 2 * 100)
        upper = np.percentile(arr, (1 - alpha / 2) * 100)
        return {
            "mean": round(float(np.mean(arr)), 4),
            "median": round(float(np.median(arr)), 4),
            "ci_lower": round(float(lower), 4),
            "ci_upper": round(float(upper), 4),
            "std": round(float(np.std(arr)), 4),
        }

    sharpe_ci = ci(sharpes)
    mdd_ci = ci(max_drawdowns)

    # Robustness criteria: 95% CI lower Sharpe > 0.5 AND MDD upper < 20%
    robust = sharpe_ci["ci_lower"] > 0.5 and mdd_ci["ci_upper"] < 0.20

    return {
        "simulations": n_simulations,
        "confidence_level": confidence_level,
        "trades_analyzed": len(trade_returns),
        "sharpe": sharpe_ci,
        "max_drawdown": mdd_ci,
        "total_return": ci(total_returns),
        "win_rate": ci(win_rates),
        "profit_factor": ci(profit_factors),
        "robust": robust,
        "robustness_criteria": {
            "sharpe_ci_lower_min": 0.5,
            "mdd_ci_upper_max": 0.20,
        },
    }
