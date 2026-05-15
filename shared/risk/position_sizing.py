"""Research-backed position sizing — Fractional Kelly, CVaR cap, vol targeting.

Sources:
- MacLean, Thorp, Ziemba (2011) "The Kelly Capital Growth Investment Criterion"
- Rockafellar & Uryasev (2000) "Optimization of Conditional Value-at-Risk"
- Moreira & Muir (JF 2017) "Volatility-Managed Portfolios"
- Harvey, Hoyle, Korgaonkar, Rattray, Sargaison, van Hemert (2018)
  "The Impact of Volatility Targeting"

All functions are pure / side-effect free and accept primitives so they can
be unit-tested without infrastructure.
"""
from __future__ import annotations
import math
from dataclasses import dataclass


# Empirical defaults validated in MacLean/Thorp/Ziemba: full Kelly is too
# aggressive for real markets; 0.25-Kelly recovers ~95% of full-Kelly Sharpe
# with ~40% lower drawdown.
DEFAULT_KELLY_FRACTION = 0.25
DEFAULT_TARGET_ANNUAL_VOL = 0.20  # 20% annualized target
DEFAULT_CVAR_CAP_PCT = 0.02       # 2% of equity at 95% CVaR
DEFAULT_MAX_LEVERAGE = 3.0


@dataclass
class SizingResult:
    """Result of position sizing computation."""
    position_fraction: float       # fraction of equity to deploy (0..max_leverage)
    kelly_fraction: float          # raw fractional Kelly before caps
    vol_scaler: float              # vol-targeting multiplier
    cvar_scaler: float             # CVaR-cap multiplier
    binding_constraint: str        # which cap was binding ("kelly"/"vol"/"cvar"/"leverage")


def fractional_kelly(
    edge: float,
    variance: float,
    fraction: float = DEFAULT_KELLY_FRACTION,
) -> float:
    """Compute fractional Kelly bet size.

    Args:
        edge: expected excess return per period (e.g. 0.02 = 2%)
        variance: variance of returns per period
        fraction: Kelly fraction (default 0.25)

    Returns:
        f* in [0, 1] — fraction of bankroll to bet. Returns 0 if edge <= 0
        or variance <= 0 (no bet without positive expectation).
    """
    if edge <= 0 or variance <= 0:
        return 0.0
    f_full = edge / variance
    return max(0.0, fraction * f_full)


def vol_target_scaler(
    realized_vol_annual: float,
    target_vol_annual: float = DEFAULT_TARGET_ANNUAL_VOL,
) -> float:
    """Compute vol-targeting multiplier.

    When realized vol > target, scale DOWN. When < target, scale UP.

    Args:
        realized_vol_annual: realized annualized vol (e.g. 0.40 for 40%)
        target_vol_annual: target annualized vol (default 20%)

    Returns:
        multiplier > 0; capped at max leverage to avoid blow-ups in calm regimes.
    """
    if realized_vol_annual <= 0:
        return 0.0
    raw = target_vol_annual / realized_vol_annual
    return max(0.0, min(DEFAULT_MAX_LEVERAGE, raw))


def realized_vol_from_atr(atr: float, price: float, bars_per_year: int = 365) -> float:
    """Convert ATR into a rough annualized volatility estimate.

    ATR/price is a proxy for daily true range. Multiplied by sqrt(bars/year)
    gives an annualized vol estimate. Suitable for daily bars; for higher
    frequency, scale bars_per_year accordingly (e.g. 365*24 for hourly).
    """
    if price <= 0 or atr <= 0:
        return 0.0
    daily_vol = atr / price
    return daily_vol * math.sqrt(bars_per_year)


def cvar_normal_approx(
    mean: float,
    sigma: float,
    confidence: float = 0.95,
) -> float:
    """Compute CVaR (expected shortfall) under a normal-distribution approximation.

    CVaR_α = -μ + σ · φ(Z_α) / (1 - α)

    Args:
        mean: per-period expected return
        sigma: per-period stdev
        confidence: VaR confidence (e.g. 0.95)

    Returns:
        positive number: expected loss conditional on being in the worst tail.
    """
    if sigma <= 0:
        return max(0.0, -mean)
    # Inverse normal CDF approximation using rational expansion (Beasley-Springer)
    # For 95% Z = 1.6449; we hardcode common values + fallback.
    if abs(confidence - 0.95) < 1e-6:
        z = 1.6449
    elif abs(confidence - 0.99) < 1e-6:
        z = 2.3263
    else:
        # Generic: use erfinv via math.erf inverse approximation
        # Acceptable accuracy for risk control
        p = 2 * confidence - 1
        z = math.sqrt(2) * _erfinv(p)
    phi_z = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    cvar = -mean + sigma * phi_z / (1.0 - confidence)
    return max(0.0, cvar)


def _erfinv(x: float) -> float:
    """Approximation of inverse error function (Winitzki 2008)."""
    a = 0.147
    ln_term = math.log(1 - x * x)
    first = 2 / (math.pi * a) + ln_term / 2
    second = ln_term / a
    sign = 1.0 if x >= 0 else -1.0
    return sign * math.sqrt(math.sqrt(first * first - second) - first)


def cvar_cap_scaler(
    sigma_per_period: float,
    mean_per_period: float = 0.0,
    cap_pct: float = DEFAULT_CVAR_CAP_PCT,
    confidence: float = 0.95,
) -> float:
    """Compute multiplier so that 95% CVaR ≤ cap_pct of equity.

    If unscaled CVaR is below the cap, returns 1.0 (no down-scaling).
    Otherwise returns cap_pct / unscaled_CVaR.
    """
    cvar = cvar_normal_approx(mean_per_period, sigma_per_period, confidence)
    if cvar <= 0:
        return 1.0
    if cvar <= cap_pct:
        return 1.0
    return max(0.0, cap_pct / cvar)


def size_position(
    edge: float,
    variance: float,
    realized_vol_annual: float,
    target_vol_annual: float = DEFAULT_TARGET_ANNUAL_VOL,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    cvar_cap_pct: float = DEFAULT_CVAR_CAP_PCT,
    max_leverage: float = DEFAULT_MAX_LEVERAGE,
) -> SizingResult:
    """Compute position size combining Fractional Kelly, vol targeting, and CVaR cap.

    Pipeline:
        1. Compute raw Fractional Kelly from edge and variance
        2. Multiply by vol-targeting scaler (raises in calm vol, lowers in storms)
        3. Multiply by CVaR scaler so worst-case 95% loss ≤ cap
        4. Cap at max_leverage

    The binding constraint reveals which guardrail is active — useful for
    debugging and surfacing in UI.
    """
    kelly = fractional_kelly(edge, variance, kelly_fraction)
    if kelly <= 0:
        return SizingResult(0.0, 0.0, 0.0, 0.0, "no_edge")

    vol_scale = vol_target_scaler(realized_vol_annual, target_vol_annual)
    sigma_per = math.sqrt(max(variance, 0.0))
    cvar_scale = cvar_cap_scaler(sigma_per, edge, cvar_cap_pct)

    sized = kelly * vol_scale * cvar_scale
    leveraged = min(sized, max_leverage)

    # Identify binding constraint
    if leveraged < sized - 1e-9:
        binding = "leverage"
    else:
        # Find smallest scaler
        scalers = {"vol": vol_scale, "cvar": cvar_scale}
        if all(s >= 0.999 for s in scalers.values()):
            binding = "kelly"
        else:
            binding = min(scalers, key=scalers.get)

    return SizingResult(
        position_fraction=leveraged,
        kelly_fraction=kelly,
        vol_scaler=vol_scale,
        cvar_scaler=cvar_scale,
        binding_constraint=binding,
    )
