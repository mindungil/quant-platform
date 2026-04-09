"""Backtest performance metrics.

Pure functions over a return series. Uses scipy where available but degrades
gracefully (the only hard dependency is numpy).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _to_array(returns) -> np.ndarray:
    arr = np.asarray(returns, dtype=float)
    return arr[np.isfinite(arr)]


def annualization_factor(periods_per_year: int) -> float:
    return float(math.sqrt(periods_per_year))


def sharpe_ratio(returns, periods_per_year: int = 252, rf: float = 0.0) -> float:
    arr = _to_array(returns)
    if len(arr) < 2:
        return 0.0
    excess = arr - rf / periods_per_year
    std = excess.std(ddof=1)
    if std <= 1e-12:
        return 0.0
    return float(excess.mean() / std * annualization_factor(periods_per_year))


def sortino_ratio(returns, periods_per_year: int = 252, rf: float = 0.0) -> float:
    arr = _to_array(returns)
    if len(arr) < 2:
        return 0.0
    excess = arr - rf / periods_per_year
    downside = excess[excess < 0]
    if len(downside) < 2:
        return 0.0
    dstd = downside.std(ddof=1)
    if dstd <= 1e-12:
        return 0.0
    return float(excess.mean() / dstd * annualization_factor(periods_per_year))


def max_drawdown(returns) -> float:
    arr = _to_array(returns)
    if len(arr) == 0:
        return 0.0
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(abs(dd.min()))


def calmar_ratio(returns, periods_per_year: int = 252) -> float:
    arr = _to_array(returns)
    if len(arr) == 0:
        return 0.0
    cum = np.prod(1.0 + arr)
    years = len(arr) / periods_per_year
    cagr = cum ** (1.0 / years) - 1.0 if years > 0 else 0.0
    mdd = max_drawdown(arr)
    if mdd <= 1e-12:
        return 0.0
    return float(cagr / mdd)


def profit_factor(returns) -> float:
    arr = _to_array(returns)
    wins = arr[arr > 0].sum()
    losses = -arr[arr < 0].sum()
    if losses <= 1e-12:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def win_rate(returns) -> float:
    arr = _to_array(returns)
    if len(arr) == 0:
        return 0.0
    return float((arr > 0).mean())


def deflated_sharpe_ratio(
    sharpe_observed: float,
    n_observations: int,
    n_trials: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado 2014).

    Adjusts an observed Sharpe ratio for the number of strategy trials run
    and for non-normality of returns. Returns a probability in [0, 1] that
    the true Sharpe is greater than zero given the observation.

    A value above 0.95 is the conventional bar for "statistically significant
    after multiple-testing correction".
    """
    if n_observations < 30 or n_trials < 1:
        return 0.0

    try:
        from scipy.stats import norm
    except Exception:
        norm = None

    # Expected maximum Sharpe from N trials assuming all true Sharpes are 0
    # (the multiple-testing inflation term). For n_trials==1 the inflation
    # is zero — there's no multiple-testing penalty.
    if n_trials <= 1:
        e_max = 0.0
    elif norm is not None:
        euler_mascheroni = 0.5772156649
        # Bailey & López de Prado 2014 closed-form approximation
        e_max = (1.0 - euler_mascheroni) * norm.ppf(1.0 - 1.0 / n_trials) + (
            euler_mascheroni * norm.ppf(1.0 - 1.0 / (n_trials * math.e))
        )
    else:
        e_max = math.sqrt(2.0 * math.log(max(n_trials, 2)))

    # Variance of Sharpe estimator under non-normality (Mertens 2002)
    var_sr = (
        1.0
        - skewness * sharpe_observed
        + ((kurtosis - 1.0) / 4.0) * (sharpe_observed ** 2)
    ) / max(n_observations - 1, 1)
    if var_sr <= 0:
        return 0.0

    z = (sharpe_observed - e_max) / math.sqrt(var_sr)
    if norm is not None:
        return float(norm.cdf(z))
    # Fallback: logistic approximation of normal CDF
    return float(1.0 / (1.0 + math.exp(-1.702 * z)))


def all_metrics(
    returns,
    periods_per_year: int = 252,
    rf: float = 0.0,
    n_trials: int = 1,
) -> dict[str, float]:
    arr = _to_array(returns)
    if len(arr) == 0:
        return {
            "sharpe": 0.0,
            "sortino": 0.0,
            "calmar": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "total_return": 0.0,
            "cagr": 0.0,
            "n_obs": 0,
            "deflated_sharpe_pvalue": 0.0,
            "skewness": 0.0,
            "kurtosis": 3.0,
        }
    sh = sharpe_ratio(arr, periods_per_year, rf)
    so = sortino_ratio(arr, periods_per_year, rf)
    mdd = max_drawdown(arr)
    pf = profit_factor(arr)
    wr = win_rate(arr)
    cum = np.prod(1.0 + arr)
    years = len(arr) / periods_per_year
    cagr = cum ** (1.0 / years) - 1.0 if years > 0 else 0.0
    cal = (cagr / mdd) if mdd > 1e-12 else 0.0

    # Higher moments
    if len(arr) >= 4:
        m = arr.mean()
        s = arr.std(ddof=1)
        if s > 1e-12:
            skew = float(((arr - m) ** 3).mean() / s ** 3)
            kurt = float(((arr - m) ** 4).mean() / s ** 4)
        else:
            skew, kurt = 0.0, 3.0
    else:
        skew, kurt = 0.0, 3.0

    dsr = deflated_sharpe_ratio(sh, len(arr), n_trials, skew, kurt)

    return {
        "sharpe": round(sh, 4),
        "sortino": round(so, 4),
        "calmar": round(cal, 4),
        "max_drawdown": round(mdd, 4),
        "profit_factor": round(min(pf, 9999.0), 4),
        "win_rate": round(wr, 4),
        "total_return": round(float(cum - 1.0), 4),
        "cagr": round(float(cagr), 4),
        "n_obs": int(len(arr)),
        "deflated_sharpe_pvalue": round(float(dsr), 4),
        "skewness": round(skew, 4),
        "kurtosis": round(kurt, 4),
    }
