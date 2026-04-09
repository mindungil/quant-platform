"""Market microstructure features.

Pure-numpy estimators that work on OHLCV bars (no order-book data needed).
References:
- Amihud (2002): Illiquidity and stock returns
- Roll (1984): A simple implicit measure of the effective bid-ask spread
- Corwin & Schultz (2012): A simple way to estimate bid-ask spreads from
  daily high and low prices
- Kyle (1985): Continuous auctions and insider trading
- Easley, López de Prado, O'Hara (2012): Flow toxicity and liquidity (VPIN)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def amihud_illiquidity(close: pd.Series, volume: pd.Series, window: int = 24) -> pd.Series:
    """Amihud illiquidity = |return| / dollar_volume, rolling-averaged.

    High values → illiquid → larger price impact for given volume.
    """
    ret = close.pct_change().abs()
    dollar_vol = (close * volume).replace(0, np.nan)
    illiq = (ret / dollar_vol).fillna(0.0)
    return illiq.rolling(window, min_periods=max(2, window // 2)).mean().fillna(0.0)


def roll_spread(close: pd.Series, window: int = 48) -> pd.Series:
    """Roll's effective spread estimator.

    s = 2 * sqrt(-cov(Δp_t, Δp_{t-1}))   when negative auto-cov
    Returns 0 if the auto-cov is positive (spread can't be estimated).
    """
    diff = close.diff()
    cov = diff.rolling(window, min_periods=window).apply(
        lambda x: np.cov(x[1:], x[:-1])[0, 1] if len(x) > 2 else 0.0,
        raw=True,
    )
    spread = 2.0 * np.sqrt(np.maximum(-cov, 0.0))
    return spread.fillna(0.0)


def corwin_schultz_spread(high: pd.Series, low: pd.Series) -> pd.Series:
    """Corwin-Schultz bid-ask spread estimator using two-day high/low ratios.

    β  = sum over 2 days of (ln(H_t/L_t))^2
    γ  = (ln(H_{t,t+1}/L_{t,t+1}))^2
    α  = (sqrt(2β) - sqrt(β)) / (3 - 2*sqrt(2)) - sqrt(γ / (3 - 2*sqrt(2)))
    spread = 2*(e^α - 1) / (1 + e^α)
    """
    h = high.astype(float)
    l = low.astype(float).replace(0, np.nan)
    log_hl = np.log(h / l)
    beta = (log_hl ** 2 + (log_hl.shift(1) ** 2)).fillna(0.0)
    h2 = h.rolling(2).max()
    l2 = l.rolling(2).min()
    gamma = (np.log(h2 / l2.replace(0, np.nan))) ** 2
    den = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / den - np.sqrt(gamma / den)
    alpha = alpha.fillna(0.0)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.clip(lower=0.0).fillna(0.0)


def kyle_lambda(close: pd.Series, volume: pd.Series, window: int = 48) -> pd.Series:
    """Kyle's lambda — price impact per unit signed dollar volume.

    Estimated as |Δlog price| / sqrt(dollar volume), rolling mean. The
    intuition: in noisy environments large volume should still be needed
    to move price; lambda quantifies how much.
    """
    ret = np.log(close / close.shift(1)).abs()
    dv = (close * volume).clip(lower=1e-9)
    lam = ret / np.sqrt(dv)
    return lam.rolling(window, min_periods=max(2, window // 2)).mean().fillna(0.0)


def signed_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Tick rule signed volume: +V if up bar, -V if down bar."""
    sign = np.sign(close.diff()).fillna(0.0)
    return (sign * volume).fillna(0.0)


def vpin_proxy(
    close: pd.Series,
    volume: pd.Series,
    bucket: int = 50,
    window: int = 50,
) -> pd.Series:
    """Volume-synchronized Probability of Informed Trading (proxy).

    Approximates the bulk-classification VPIN metric on time bars without
    constructing volume buckets explicitly. Uses tick-rule signed volume
    over rolling windows: VPIN ≈ |Σ signed_volume| / Σ |volume|.
    Range [0, 1]; higher = more order-flow toxicity.
    """
    sv = signed_volume(close, volume)
    abs_v = volume.abs().replace(0, np.nan)
    num = sv.rolling(window, min_periods=window).sum().abs()
    den = abs_v.rolling(window, min_periods=window).sum()
    vpin = (num / den).clip(0.0, 1.0).fillna(0.0)
    return vpin


def high_low_volatility(high: pd.Series, low: pd.Series, window: int = 24) -> pd.Series:
    """Parkinson 1980 high-low range volatility estimator (annualized factor omitted).

    Returns rolling-mean of (ln(H/L))^2 / (4 ln 2).
    """
    log_hl = np.log(high.astype(float) / low.astype(float).replace(0, np.nan))
    pk = (log_hl ** 2) / (4.0 * np.log(2.0))
    return pk.rolling(window, min_periods=max(2, window // 2)).mean().fillna(0.0)
