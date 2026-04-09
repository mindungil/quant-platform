"""Statistical validation engine for backtest results.

Provides ADF stationarity tests, autocorrelation analysis, regression
alpha/beta decomposition, and a comprehensive backtest validation report
to detect overfitting and assess strategy robustness.
"""

from __future__ import annotations

import logging
import math

import numpy as np
from scipy import stats as sp_stats
from statsmodels.api import OLS, add_constant
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.tsa.stattools import adfuller

logger = logging.getLogger(__name__)

_MIN_SAMPLES = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_array(series: list[float] | np.ndarray) -> np.ndarray:
    arr = np.asarray(series, dtype=np.float64)
    return arr[np.isfinite(arr)]


def _insufficient(arr: np.ndarray) -> bool:
    return len(arr) < _MIN_SAMPLES


# ---------------------------------------------------------------------------
# 1. Stationarity — Augmented Dickey-Fuller
# ---------------------------------------------------------------------------


def test_stationarity(series: list[float]) -> dict:
    """Run the Augmented Dickey-Fuller test on *series*.

    Returns a dict with adf_statistic, p_value, is_stationary, and
    critical_values.  When the input is too short or degenerate, returns
    a report with ``is_stationary=False`` and a descriptive error.
    """
    arr = _to_array(series)

    if len(arr) == 0:
        return {"adf_statistic": None, "p_value": None,
                "is_stationary": False, "critical_values": {},
                "error": "empty series"}

    if _insufficient(arr):
        return {"adf_statistic": None, "p_value": None,
                "is_stationary": False, "critical_values": {},
                "error": f"insufficient data ({len(arr)} samples, need {_MIN_SAMPLES})"}

    if np.std(arr) == 0:
        return {"adf_statistic": None, "p_value": None,
                "is_stationary": True, "critical_values": {},
                "error": "constant series (trivially stationary)"}

    try:
        result = adfuller(arr, autolag="AIC")
        adf_stat, p_value, _usedlag, _nobs, crit_values, _icbest = result
        return {
            "adf_statistic": round(float(adf_stat), 6),
            "p_value": round(float(p_value), 6),
            "is_stationary": bool(p_value < 0.05),
            "critical_values": {k: round(float(v), 6) for k, v in crit_values.items()},
        }
    except Exception as exc:
        logger.warning("ADF test failed: %s", exc)
        return {"adf_statistic": None, "p_value": None,
                "is_stationary": False, "critical_values": {},
                "error": str(exc)}


# ---------------------------------------------------------------------------
# 2. Autocorrelation — Ljung-Box
# ---------------------------------------------------------------------------


def test_autocorrelation(returns: list[float], lags: int = 20) -> dict:
    """Ljung-Box test for serial correlation in *returns*.

    Returns lb_statistic, lb_pvalue (arrays), has_autocorrelation, and
    significant_lags (list of lag indices where p < 0.05).
    """
    arr = _to_array(returns)

    if len(arr) == 0:
        return {"lb_statistic": [], "lb_pvalue": [],
                "has_autocorrelation": False, "significant_lags": [],
                "error": "empty series"}

    if _insufficient(arr):
        return {"lb_statistic": [], "lb_pvalue": [],
                "has_autocorrelation": False, "significant_lags": [],
                "error": f"insufficient data ({len(arr)} samples, need {_MIN_SAMPLES})"}

    if np.std(arr) == 0:
        return {"lb_statistic": [], "lb_pvalue": [],
                "has_autocorrelation": False, "significant_lags": [],
                "error": "constant series"}

    try:
        effective_lags = min(lags, len(arr) // 2 - 1)
        if effective_lags < 1:
            effective_lags = 1

        result = acorr_ljungbox(arr, lags=effective_lags, return_df=True)
        lb_stat = result["lb_stat"].tolist()
        lb_pval = result["lb_pvalue"].tolist()

        significant = [i + 1 for i, p in enumerate(lb_pval) if p < 0.05]

        return {
            "lb_statistic": [round(v, 6) for v in lb_stat],
            "lb_pvalue": [round(v, 6) for v in lb_pval],
            "has_autocorrelation": len(significant) > 0,
            "significant_lags": significant,
        }
    except Exception as exc:
        logger.warning("Ljung-Box test failed: %s", exc)
        return {"lb_statistic": [], "lb_pvalue": [],
                "has_autocorrelation": False, "significant_lags": [],
                "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. Regression alpha / beta
# ---------------------------------------------------------------------------


def regression_alpha_beta(
    strategy_returns: list[float],
    benchmark_returns: list[float],
) -> dict:
    """OLS regression: strategy = alpha + beta * benchmark + epsilon.

    Alpha is annualised (x 252).  Returns alpha, beta, r_squared,
    alpha_pvalue, and is_alpha_significant.
    """
    strat = _to_array(strategy_returns)
    bench = _to_array(benchmark_returns)

    n = min(len(strat), len(bench))

    if n == 0:
        return {"alpha": None, "beta": None, "r_squared": None,
                "alpha_pvalue": None, "is_alpha_significant": False,
                "error": "empty series"}

    if n < _MIN_SAMPLES:
        return {"alpha": None, "beta": None, "r_squared": None,
                "alpha_pvalue": None, "is_alpha_significant": False,
                "error": f"insufficient data ({n} samples, need {_MIN_SAMPLES})"}

    strat = strat[:n]
    bench = bench[:n]

    if np.std(bench) == 0:
        return {"alpha": None, "beta": None, "r_squared": None,
                "alpha_pvalue": None, "is_alpha_significant": False,
                "error": "benchmark has zero variance"}

    try:
        X = add_constant(bench)
        model = OLS(strat, X).fit()
        daily_alpha = float(model.params[0])
        beta = float(model.params[1])
        alpha_annualised = daily_alpha * 252
        alpha_pvalue = float(model.pvalues[0])
        r_sq = float(model.rsquared)

        return {
            "alpha": round(alpha_annualised, 6),
            "beta": round(beta, 6),
            "r_squared": round(r_sq, 6),
            "alpha_pvalue": round(alpha_pvalue, 6),
            "is_alpha_significant": alpha_pvalue < 0.05,
        }
    except Exception as exc:
        logger.warning("Regression failed: %s", exc)
        return {"alpha": None, "beta": None, "r_squared": None,
                "alpha_pvalue": None, "is_alpha_significant": False,
                "error": str(exc)}


# ---------------------------------------------------------------------------
# 4. Comprehensive backtest validation
# ---------------------------------------------------------------------------


def _hurst_exponent(returns: np.ndarray) -> float:
    """Estimate Hurst exponent via rescaled-range (R/S) analysis.

    H > 0.5 => trending, H < 0.5 => mean-reverting, H ~ 0.5 => random walk.
    """
    n = len(returns)
    if n < 20:
        return 0.5  # not enough data, assume random walk

    # Use R/S analysis across multiple sub-series lengths
    min_window = 10
    max_window = n // 2
    if max_window < min_window:
        return 0.5

    window_sizes = []
    rs_values = []

    window = min_window
    while window <= max_window:
        n_windows = n // window
        if n_windows < 1:
            break
        rs_list = []
        for i in range(n_windows):
            sub = returns[i * window:(i + 1) * window]
            mean_sub = np.mean(sub)
            deviate = np.cumsum(sub - mean_sub)
            r = np.max(deviate) - np.min(deviate)
            s = np.std(sub, ddof=1)
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            window_sizes.append(window)
            rs_values.append(np.mean(rs_list))
        window = int(window * 1.5)
        if window == int(window / 1.5):
            window += 1

    if len(window_sizes) < 3:
        return 0.5

    log_n = np.log(window_sizes)
    log_rs = np.log(rs_values)
    slope, _intercept, _r, _p, _se = sp_stats.linregress(log_n, log_rs)
    hurst = float(slope)
    return max(0.0, min(1.0, hurst))


def _max_drawdown_duration(returns: np.ndarray) -> int:
    """Return the longest drawdown duration in number of periods."""
    if len(returns) == 0:
        return 0

    cumulative = np.cumprod(1.0 + returns)
    running_max = np.maximum.accumulate(cumulative)
    in_drawdown = cumulative < running_max

    max_dur = 0
    current_dur = 0
    for dd in in_drawdown:
        if dd:
            current_dur += 1
            max_dur = max(max_dur, current_dur)
        else:
            current_dur = 0
    return max_dur


def validate_backtest(
    returns: list[float],
    benchmark_returns: list[float] | None = None,
) -> dict:
    """Run comprehensive statistical validation on backtest returns.

    Produces a report dict containing results from all individual tests
    plus Hurst exponent, Jarque-Bera normality test, max drawdown
    duration, and an overall confidence score (0-1).
    """
    arr = _to_array(returns)

    if len(arr) == 0:
        return {"error": "empty returns", "confidence": 0.0,
                "tests": {}, "summary": "no data to validate"}

    if _insufficient(arr):
        return {"error": f"insufficient data ({len(arr)} samples)",
                "confidence": 0.0, "tests": {},
                "summary": "need at least 30 return observations"}

    if np.all(arr == 0):
        return {"error": "all-zero returns", "confidence": 0.0,
                "tests": {}, "summary": "strategy produced no returns"}

    report: dict = {"tests": {}, "confidence": 0.0}

    # --- Stationarity of returns ---
    adf = test_stationarity(arr.tolist())
    report["tests"]["stationarity"] = adf

    # --- Autocorrelation ---
    acorr = test_autocorrelation(arr.tolist())
    report["tests"]["autocorrelation"] = acorr

    # --- Regression (if benchmark provided) ---
    if benchmark_returns is not None:
        bench_arr = _to_array(benchmark_returns)
        if len(bench_arr) >= _MIN_SAMPLES:
            reg = regression_alpha_beta(arr.tolist(), bench_arr.tolist())
            report["tests"]["regression"] = reg

    # --- Hurst exponent ---
    hurst = _hurst_exponent(arr)
    report["hurst_exponent"] = round(hurst, 4)

    # --- Jarque-Bera normality test ---
    try:
        jb_stat, jb_pvalue = sp_stats.jarque_bera(arr)
        report["tests"]["jarque_bera"] = {
            "statistic": round(float(jb_stat), 6),
            "p_value": round(float(jb_pvalue), 6),
            "is_normal": float(jb_pvalue) >= 0.05,
        }
    except Exception as exc:
        report["tests"]["jarque_bera"] = {
            "statistic": None, "p_value": None,
            "is_normal": False, "error": str(exc),
        }

    # --- Max drawdown duration ---
    report["max_drawdown_duration"] = _max_drawdown_duration(arr)

    # --- Confidence score (0-1) ---
    score = 0.0
    checks = 0

    # Returns should be stationary (good sign of real edge, not drift)
    if adf.get("is_stationary"):
        score += 1.0
    checks += 1

    # Low autocorrelation is desirable (no look-ahead bias / data leakage)
    if not acorr.get("has_autocorrelation"):
        score += 1.0
    checks += 1

    # Hurst away from 0.5 suggests non-random behaviour
    hurst_deviation = abs(hurst - 0.5)
    if hurst_deviation > 0.1:
        score += min(hurst_deviation / 0.3, 1.0)
    checks += 1

    # Non-normal returns are expected for real strategies
    jb = report["tests"].get("jarque_bera", {})
    if not jb.get("is_normal", True):
        score += 0.5
    checks += 1

    # Moderate drawdown duration (not too long relative to sample)
    dd_ratio = report["max_drawdown_duration"] / len(arr)
    if dd_ratio < 0.5:
        score += 1.0
    elif dd_ratio < 0.75:
        score += 0.5
    checks += 1

    # Significant alpha is a strong signal
    reg = report["tests"].get("regression", {})
    if reg.get("is_alpha_significant"):
        score += 1.5
        checks += 1.5
    elif "regression" in report["tests"]:
        checks += 1.5

    confidence = round(score / checks, 4) if checks > 0 else 0.0
    report["confidence"] = confidence

    # Summary
    findings = []
    if adf.get("is_stationary"):
        findings.append("returns are stationary")
    else:
        findings.append("returns are non-stationary (possible unit root)")

    if acorr.get("has_autocorrelation"):
        findings.append(f"autocorrelation detected at lags {acorr['significant_lags']}")
    else:
        findings.append("no significant autocorrelation")

    findings.append(f"Hurst={hurst:.3f} ({'trending' if hurst > 0.55 else 'mean-reverting' if hurst < 0.45 else 'random-walk-like'})")

    if reg.get("is_alpha_significant"):
        findings.append(f"significant alpha={reg['alpha']:.4f}")
    elif "regression" in report["tests"]:
        findings.append("alpha not statistically significant")

    report["summary"] = "; ".join(findings)

    return report
