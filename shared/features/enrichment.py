"""Cross-market enrichment features for the signal layer.

Each helper takes a panel (or single-asset DataFrame) of OHLCV and produces
a per-bar causal feature series. Designed to augment alpha inputs without
touching the alpha bodies — callers apply them as additional columns or
pass them through `shared.portfolio.meta_ensemble` as regime priors.

Features:

  - btc_dominance(panel)           : BTC_marketcap_proxy / total_proxy
  - dispersion_eigenratio(returns) : λ_1 / Σλ of rolling corr matrix
                                     (1.0 = all-correlated; low = dispersion)
  - funding_return_divergence(df)  : funding signal vs realized return
  - volume_obv_regime(df)          : z-score of (Δclose * volume) cumulative

All outputs are pd.Series indexed on the input timeline, causal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def btc_dominance(
    panel: dict[str, pd.DataFrame],
    btc_key: str = "BTCUSDT",
    window: int = 168,
) -> pd.Series:
    """Rolling proxy for BTC dominance: BTC notional / total notional.

    `notional_t = price_t * volume_t`. Uses a rolling sum to smooth tick noise.
    Returned as a fraction in [0, 1]; typical BTC dominance is 0.4–0.6.
    """
    if btc_key not in panel:
        raise KeyError(f"panel missing {btc_key}")

    btc = panel[btc_key]
    idx = btc.index

    totals: pd.Series = pd.Series(0.0, index=idx)
    btc_notional: pd.Series = pd.Series(0.0, index=idx)
    for name, df in panel.items():
        aligned = df.reindex(idx)
        notional = (aligned["close"] * aligned.get("volume", 0.0)).fillna(0.0)
        totals = totals + notional
        if name == btc_key:
            btc_notional = notional

    btc_roll = btc_notional.rolling(window, min_periods=max(20, window // 4)).sum()
    tot_roll = totals.rolling(window, min_periods=max(20, window // 4)).sum().replace(0, np.nan)
    return (btc_roll / tot_roll).fillna(0.5).clip(0.0, 1.0)


def dispersion_eigenratio(
    panel: dict[str, pd.DataFrame],
    window: int = 168,
) -> pd.Series:
    """Rolling eigen-ratio λ_1 / Σ λ of the cross-asset return correlation.

    Value 1.0 → perfect co-movement (risk-off, 'everything selling');
    value near 1/K (K = #assets) → decorrelation (dispersion, stock-picking
    regime). This is the classical crisis early-warning signal (Billio et al).
    """
    ret_panel = pd.DataFrame(
        {name: df["close"].pct_change().fillna(0.0) for name, df in panel.items()}
    )
    ret_panel = ret_panel.dropna(how="all")
    idx = ret_panel.index
    n = len(idx)
    out = np.full(n, np.nan)
    min_bars = max(20, window // 4)

    for i in range(min_bars, n):
        lo = max(0, i - window)
        sub = ret_panel.iloc[lo:i]
        if sub.shape[1] < 2:
            continue
        vals = sub.values
        if not np.all(np.isfinite(vals)):
            continue
        std = vals.std(axis=0, ddof=0)
        if np.any(std < 1e-12):
            continue
        corr = np.corrcoef(vals, rowvar=False)
        eigvals = np.linalg.eigvalsh(corr)
        eigvals = np.clip(eigvals, 0.0, None)
        tot = eigvals.sum()
        if tot <= 0:
            continue
        out[i] = float(eigvals.max() / tot)

    return pd.Series(out, index=idx).ffill().bfill()


def funding_return_divergence(
    df: pd.DataFrame,
    window: int = 48,
) -> pd.Series:
    """Divergence between funding rate signal and realized return sign.

    Expects a `funding` column (annualized rate, decimal). Output:
    z-score of (funding * sign(return)) over `window` — positive when
    longs ARE profitable despite paying funding (potential carry opportunity),
    negative when longs lose AND pay funding (double-bad, avoid).
    """
    funding_col = "funding" if "funding" in df.columns else ("funding_rate" if "funding_rate" in df.columns else None)
    if funding_col is None:
        return pd.Series(0.0, index=df.index)

    ret = df["close"].pct_change().fillna(0.0)
    signed = df[funding_col].astype(float) * np.sign(ret).replace(0, 1.0)
    mean = signed.rolling(window, min_periods=max(10, window // 4)).mean()
    std = signed.rolling(window, min_periods=max(10, window // 4)).std(ddof=0).replace(0, np.nan)
    z = ((signed - mean) / std).fillna(0.0)
    return z.clip(-5.0, 5.0)


def volume_obv_regime(df: pd.DataFrame, window: int = 168) -> pd.Series:
    """Rolling z-score of OBV slope.

    OBV = cumulative Σ sign(Δclose) * volume. Positive z = accumulation
    (rising OBV slope), negative z = distribution. Used as a liquidity-
    regime feature: accumulation supports trend-followers, distribution
    supports caution.
    """
    close = df["close"].astype(float)
    vol = df.get("volume", 1.0).astype(float)
    dir_ = np.sign(close.diff()).fillna(0.0)
    obv = (dir_ * vol).cumsum()
    slope = obv.diff(window) / window
    mean = slope.rolling(window, min_periods=max(10, window // 4)).mean()
    std = slope.rolling(window, min_periods=max(10, window // 4)).std(ddof=0).replace(0, np.nan)
    z = ((slope - mean) / std).fillna(0.0)
    return z.clip(-5.0, 5.0)
