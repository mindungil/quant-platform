"""Forecast combination and instrument-level vol targeting.

Clean-room implementation of the forecast-combination pipeline described by
Robert Carver in "Systematic Trading" (2015) and on qoppac.blogspot.com:

    raw signal -> scaled forecast (mean abs = 10, capped at +/-20)
               -> weighted sum of multiple scaled forecasts
               -> multiplied by Forecast Diversification Multiplier (FDM)
               -> re-capped at +/-20
               -> translated to a position via instrument vol targeting

No code from pysystemtrade (GPL-3) was consulted; only the publicly described
algorithms/formulae. All functions are pure and primitive-typed.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

try:  # numpy is optional; fall back to pure python
    import numpy as _np  # type: ignore
    _HAS_NUMPY = True
except Exception:  # pragma: no cover
    _HAS_NUMPY = False


DEFAULT_TARGET_ABS_FORECAST = 10.0
DEFAULT_FORECAST_CAP_MULTIPLE = 2.0  # cap at +/- 2 * target_abs
MAX_FDM = 2.5  # Carver's recommended ceiling


def scale_forecast(raw_forecast: float, target_abs: float = DEFAULT_TARGET_ABS_FORECAST) -> float:
    """Clip a raw scaled forecast to +/- 2*target_abs.

    Assumes the caller has already divided by the historical average absolute
    forecast so that the long-run mean |forecast| == target_abs. This function
    only enforces the symmetric cap that prevents any single signal from
    dominating the combined forecast.
    """
    cap = DEFAULT_FORECAST_CAP_MULTIPLE * target_abs
    if raw_forecast != raw_forecast:  # NaN guard
        return 0.0
    if raw_forecast > cap:
        return cap
    if raw_forecast < -cap:
        return -cap
    return float(raw_forecast)


def forecast_diversification_multiplier(
    corr_matrix: List[List[float]],
    weights: Optional[List[float]] = None,
) -> float:
    """Forecast Diversification Multiplier.

        FDM = 1 / sqrt(w' C w)

    where C is the correlation matrix of the underlying forecasts and w is the
    (equal-weight by default) weight vector. Capped at MAX_FDM.
    """
    n = len(corr_matrix)
    if n == 0:
        return 1.0
    for row in corr_matrix:
        if len(row) != n:
            raise ValueError("correlation matrix must be square")

    if weights is None:
        weights = [1.0 / n] * n
    if len(weights) != n:
        raise ValueError("weights length must match correlation matrix size")

    if _HAS_NUMPY:
        w = _np.asarray(weights, dtype=float)
        c = _np.asarray(corr_matrix, dtype=float)
        variance = float(w @ c @ w)
    else:
        variance = 0.0
        for i in range(n):
            for j in range(n):
                variance += weights[i] * weights[j] * corr_matrix[i][j]

    if variance <= 0.0:
        return MAX_FDM
    fdm = 1.0 / math.sqrt(variance)
    return min(fdm, MAX_FDM)


def combine_forecasts(
    forecasts: Dict[str, float],
    weights: Optional[Dict[str, float]] = None,
    corr_matrix: Optional[Dict[Tuple[str, str], float]] = None,
    target_abs: float = DEFAULT_TARGET_ABS_FORECAST,
) -> float:
    """Combine several scaled forecasts into one capped scaled forecast.

    Steps: (1) clip each input via scale_forecast, (2) take the weighted sum
    using the supplied weights (default equal), (3) multiply by the FDM
    computed from corr_matrix (if None, assume uncorrelated -> FDM = sqrt(N)),
    (4) re-clip at +/- 2*target_abs.
    """
    if not forecasts:
        return 0.0

    names = list(forecasts.keys())
    n = len(names)

    if weights is None:
        w = {k: 1.0 / n for k in names}
    else:
        total = sum(weights.get(k, 0.0) for k in names)
        if total <= 0:
            w = {k: 1.0 / n for k in names}
        else:
            w = {k: weights.get(k, 0.0) / total for k in names}

    scaled = {k: scale_forecast(forecasts[k], target_abs) for k in names}
    weighted_sum = sum(w[k] * scaled[k] for k in names)

    if corr_matrix is None:
        fdm = min(math.sqrt(n), MAX_FDM)
    else:
        c_mat: List[List[float]] = []
        for i, a in enumerate(names):
            row: List[float] = []
            for j, b in enumerate(names):
                if i == j:
                    row.append(1.0)
                elif (a, b) in corr_matrix:
                    row.append(float(corr_matrix[(a, b)]))
                elif (b, a) in corr_matrix:
                    row.append(float(corr_matrix[(b, a)]))
                else:
                    row.append(0.0)
            c_mat.append(row)
        fdm = forecast_diversification_multiplier(c_mat, [w[k] for k in names])

    combined = weighted_sum * fdm
    return scale_forecast(combined, target_abs)


def forecast_to_position(
    combined_forecast: float,
    capital: float,
    instrument_vol: float,
    target_annual_vol: float = 0.20,
    target_abs_forecast: float = DEFAULT_TARGET_ABS_FORECAST,
    fx_rate: float = 1.0,
    point_value: float = 1.0,
) -> float:
    """Carver's instrument-level vol-targeted position sizing.

        position = (forecast / target_abs) *
                   (target_annual_vol * capital) /
                   (instrument_vol * fx_rate * point_value)

    At forecast = +/- target_abs the annualized position vol equals
    target_annual_vol * capital. Stronger forecasts scale linearly up to
    +/- 2 * target_abs.
    """
    if capital <= 0 or instrument_vol <= 0 or fx_rate <= 0 or point_value <= 0:
        return 0.0
    if target_abs_forecast <= 0:
        return 0.0
    cash_vol_target = target_annual_vol * capital
    denom = instrument_vol * fx_rate * point_value
    return (combined_forecast / target_abs_forecast) * (cash_vol_target / denom)


if __name__ == "__main__":
    print("=== scale_forecast ===")
    for v in [-30.0, -15.0, 0.0, 15.0, 30.0]:
        s = scale_forecast(v)
        print(f"scale_forecast({v:>6}) = {s}")
    assert scale_forecast(-30.0) == -20.0
    assert scale_forecast(-15.0) == -15.0
    assert scale_forecast(0.0) == 0.0
    assert scale_forecast(15.0) == 15.0
    assert scale_forecast(30.0) == 20.0

    print("\n=== FDM (3x3 with off-diagonal correlations) ===")
    corr3 = [
        [1.0, 0.5, 0.3],
        [0.5, 1.0, 0.5],
        [0.3, 0.5, 1.0],
    ]
    fdm3 = forecast_diversification_multiplier(corr3)
    print(f"fdm_3x3: {fdm3:.4f}")
    # analytic: (1/9)*(3 + 2*(0.5+0.3+0.5)) = 0.6222; 1/sqrt = 1.2678
    assert 1.20 <= fdm3 <= 1.50, f"expected ~1.27-1.40, got {fdm3}"

    print("\n=== FDM (5x5 identity, should be sqrt(5)=2.236, under cap 2.5) ===")
    ident5 = [[1.0 if i == j else 0.0 for j in range(5)] for i in range(5)]
    fdm5 = forecast_diversification_multiplier(ident5)
    print(f"fdm_5x5_identity: {fdm5:.4f}")
    assert abs(fdm5 - math.sqrt(5)) < 1e-6, f"expected sqrt(5), got {fdm5}"
    assert fdm5 <= MAX_FDM

    print("\n=== FDM cap (10x10 identity should clip to 2.5) ===")
    ident10 = [[1.0 if i == j else 0.0 for j in range(10)] for i in range(10)]
    fdm10 = forecast_diversification_multiplier(ident10)
    print(f"fdm_10x10_identity_capped: {fdm10:.4f}")
    assert fdm10 == MAX_FDM

    print("\n=== combine_forecasts (3 signals, mild correlation) ===")
    forecasts = {"momentum": 12.0, "carry": 8.0, "value": -4.0}
    corr = {
        ("momentum", "carry"): 0.2,
        ("momentum", "value"): -0.1,
        ("carry", "value"): 0.1,
    }
    combined = combine_forecasts(forecasts, corr_matrix=corr)
    print(f"combined_forecast: {combined:.4f}")
    # mean = (12+8-4)/3 = 5.333; FDM ~ 1/sqrt(w'Cw) ~ 1.55
    assert -20.0 <= combined <= 20.0
    assert 5.0 <= combined <= 12.0, f"expected ~8, got {combined}"

    print("\n=== combine_forecasts (no corr_matrix -> FDM=sqrt(N)) ===")
    combined_uncorr = combine_forecasts({"a": 10.0, "b": 10.0, "c": 10.0})
    print(f"combined_uncorrelated: {combined_uncorr:.4f}")
    # mean = 10, FDM = sqrt(3) ~ 1.732 -> 17.32
    assert 17.0 <= combined_uncorr <= 17.5

    print("\n=== combine_forecasts (extreme input, expect capped) ===")
    capped = combine_forecasts({"a": 50.0, "b": 50.0})
    print(f"combined_capped: {capped:.4f}")
    assert capped == 20.0

    print("\n=== forecast_to_position ===")
    pos = forecast_to_position(
        combined_forecast=15.0,
        capital=10000.0,
        instrument_vol=0.5,
    )
    # (15/10) * (0.20 * 10000) / (0.5 * 1 * 1) = 1.5 * 2000 / 0.5 = 6000
    print(f"position (fcst=15, cap=10k, vol=0.5): {pos:.4f}")
    assert abs(pos - 6000.0) < 1e-6, f"expected 6000, got {pos}"

    # average forecast case
    pos_avg = forecast_to_position(10.0, 10000.0, 0.5)
    print(f"position (fcst=10, cap=10k, vol=0.5): {pos_avg:.4f}")
    assert abs(pos_avg - 4000.0) < 1e-6

    # zero / negative guards
    assert forecast_to_position(10.0, 0.0, 0.5) == 0.0
    assert forecast_to_position(10.0, 10000.0, 0.0) == 0.0

    print("\nAll forecast_combination tests passed.")
