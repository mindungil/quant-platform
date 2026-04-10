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


def apply_funding_cost(
    position,
    funding_rate_per_bar,
) -> np.ndarray:
    """Compute per-bar funding cost for a perpetual futures position.

    Funding is paid by the side that has the position in the direction of
    the funding rate: if funding > 0 and you're long, you PAY funding.
    If funding > 0 and you're short, you RECEIVE funding.

    Args:
        position: target position series (positive = long, negative = short)
        funding_rate_per_bar: funding rate already amortized to the bar
            frequency (e.g. for 1h bars, divide the 8h funding rate by 8).

    Returns:
        per-bar funding cost (positive = cost, negative = income)
    """
    pos = np.asarray(position, dtype=float)
    fr = np.asarray(funding_rate_per_bar, dtype=float)
    # Long pays positive funding, short receives (and vice versa)
    return pos * fr


def market_impact_bps(
    delta_notional: float,
    bar_volume: float,
    impact_coeff: float = 0.1,
) -> float:
    """Square-root market impact model (Almgren & Chriss 2001).

    impact_bps = impact_coeff × √(|delta_notional| / bar_volume) × 10000

    For a $50k trade on a bar with $10M volume:
      impact = 0.1 × √(50000/10000000) × 10000 ≈ 7 bps

    Args:
        delta_notional: absolute change in notional ($)
        bar_volume: total bar volume ($, use quote_volume)
        impact_coeff: calibration constant (0.05-0.20 typical for crypto)

    Returns:
        additional cost in basis points (on top of fixed commission)
    """
    if bar_volume <= 0 or delta_notional <= 0:
        return 0.0
    participation = delta_notional / bar_volume
    return float(impact_coeff * np.sqrt(participation) * 10000)


def apply_transaction_costs(
    position,
    returns,
    cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
    funding_rate_per_bar=None,
    volume=None,
    notional_per_unit: float = 0.0,
    impact_coeff: float = 0.1,
) -> np.ndarray:
    """Compute net per-bar PnL after ALL costs.

    Cost layers:
      1) Fixed commission: (cost_bps + slippage_bps) × |Δposition|
      2) Market impact (if volume provided): sqrt(participation) model
      3) Funding rate (if provided): position × funding_rate_per_bar

    Args:
        position: target position series (in units of underlying notional, [-1,1])
        returns:  underlying bar returns
        cost_bps: round-trip taker fee, e.g. 4 for Binance taker
        slippage_bps: extra slippage assumption, e.g. 1-3 bps
        funding_rate_per_bar: per-bar funding rate (amortized from 8h)
        volume: per-bar quote volume (for market impact). If None, impact=0.
        notional_per_unit: $ value of 1 unit position (e.g. $100k portfolio).
            Used to convert fractional position to notional for impact calc.
        impact_coeff: sqrt-impact calibration (0.05-0.20 for crypto)

    Returns:
        net per-bar return series after all costs
    """
    pos = np.asarray(position, dtype=float)
    ret = np.asarray(returns, dtype=float)
    if len(pos) == 0:
        return np.zeros(0)
    gross_pnl = pos * ret
    delta_pos = np.abs(np.diff(pos, prepend=0.0))
    bps_total = (float(cost_bps) + float(slippage_bps)) * 1e-4
    cost = delta_pos * bps_total

    # Market impact (optional — requires volume data)
    if volume is not None and notional_per_unit > 0:
        vol_arr = np.asarray(volume, dtype=float)
        for i in range(len(pos)):
            if delta_pos[i] > 1e-8 and vol_arr[i] > 0:
                trade_notional = delta_pos[i] * notional_per_unit
                impact = market_impact_bps(trade_notional, vol_arr[i], impact_coeff) * 1e-4
                cost[i] += delta_pos[i] * impact

    net = gross_pnl - cost
    # Subtract funding cost if provided
    if funding_rate_per_bar is not None:
        fr = np.asarray(funding_rate_per_bar, dtype=float)
        if len(fr) == len(pos):
            net = net - apply_funding_cost(pos, fr)
    return net


def turnover_stats(position, periods_per_year: int = 252) -> dict[str, float]:
    """Annualized turnover (Σ|Δpos|) and per-bar mean turnover."""
    pos = np.asarray(position, dtype=float)
    if len(pos) < 2:
        return {"per_bar_turnover": 0.0, "annual_turnover": 0.0}
    delta = np.abs(np.diff(pos, prepend=0.0))
    per_bar = float(delta.mean())
    annual = float(per_bar * periods_per_year)
    return {"per_bar_turnover": per_bar, "annual_turnover": annual}


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
