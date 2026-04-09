"""Synthetic OHLCV generator for seed-time backtests.

Produces a regime-switching geometric Brownian motion with stochastic vol
(GARCH-ish via vol clustering) so backtests at seed time exercise enough
market behavior to produce meaningful pass/fail decisions without depending
on the market-data service.

Not intended as a substitute for real historical data — when real data is
available, use it. This is the seed-time fallback only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def generate_synthetic_ohlcv(
    n_bars: int = 4000,
    seed: int = 42,
    start_price: float = 30000.0,
    base_vol: float = 0.012,          # per-bar vol
    drift: float = 0.00012,           # per-bar drift
    regime_persistence: float = 0.992,  # prob of staying in current regime
    vol_clustering: float = 0.92,     # AR(1) coef on |return|
    bar: str = "1h",
    funding: bool = True,
    trend_strength: float = 5.0,      # multiplier on regime drift (higher = clearer trends)
) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame with regime switching.

    Returns columns: timestamp, open, high, low, close, volume, funding_rate (optional).
    """
    rng = np.random.default_rng(seed)
    log_prices = np.zeros(n_bars)
    log_prices[0] = np.log(start_price)

    # Regime: 0 = trending up, 1 = trending down, 2 = ranging
    regime = np.zeros(n_bars, dtype=int)
    drifts = np.array([drift * trend_strength, -drift * trend_strength, 0.0])
    vols = np.array([base_vol, base_vol * 1.4, base_vol * 0.7])

    cur_vol_factor = 1.0
    for t in range(1, n_bars):
        # Possibly switch regime
        if rng.random() > regime_persistence:
            regime[t] = rng.integers(0, 3)
        else:
            regime[t] = regime[t - 1]

        # Vol clustering AR(1)
        innov = abs(rng.normal(0, 1))
        cur_vol_factor = vol_clustering * cur_vol_factor + (1 - vol_clustering) * innov
        sigma = vols[regime[t]] * (0.5 + cur_vol_factor)

        ret = drifts[regime[t]] + sigma * rng.normal()
        log_prices[t] = log_prices[t - 1] + ret

    closes = np.exp(log_prices)

    # Build OHLCV from closes with intra-bar noise
    intra_noise = rng.normal(0, base_vol * 0.5, size=n_bars)
    highs = closes * (1.0 + np.abs(intra_noise))
    lows = closes * (1.0 - np.abs(intra_noise))
    opens = np.concatenate([[start_price], closes[:-1]])
    # Ensure OHLC ordering
    highs = np.maximum.reduce([highs, opens, closes])
    lows = np.minimum.reduce([lows, opens, closes])

    volumes = np.exp(rng.normal(15.0, 0.5, size=n_bars)) * (1.0 + 0.5 * (regime == 0))

    timestamps = pd.date_range("2020-01-01", periods=n_bars, freq=bar)

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    ).set_index("timestamp")

    if funding:
        # Synthetic funding: small persistent process around 0
        f = np.zeros(n_bars)
        for t in range(1, n_bars):
            f[t] = 0.95 * f[t - 1] + 0.0001 * rng.normal()
        df["funding_rate"] = f

    df["regime"] = regime
    return df


def generate_ranging_ohlcv(
    n_bars: int = 4000,
    seed: int = 13,
    start_price: float = 30000.0,
    vol: float = 0.014,
    mean_reversion_speed: float = 0.04,
    bar: str = "1h",
) -> pd.DataFrame:
    """Mean-reverting synthetic series for stress-testing reversion alphas.

    Uses an Ornstein-Uhlenbeck process around log(start_price). Vol regime
    switches occasionally to keep things realistic.
    """
    rng = np.random.default_rng(seed)
    log_target = np.log(start_price)
    log_p = np.zeros(n_bars)
    log_p[0] = log_target
    for t in range(1, n_bars):
        # OU drift toward target
        drift = mean_reversion_speed * (log_target - log_p[t - 1])
        # Vol regime swap roughly every 200 bars
        sigma = vol * (1.5 if (t // 200) % 3 == 0 else 1.0)
        log_p[t] = log_p[t - 1] + drift + sigma * rng.normal()

    closes = np.exp(log_p)
    intra = rng.normal(0, vol * 0.5, size=n_bars)
    highs = closes * (1.0 + np.abs(intra))
    lows = closes * (1.0 - np.abs(intra))
    opens = np.concatenate([[start_price], closes[:-1]])
    highs = np.maximum.reduce([highs, opens, closes])
    lows = np.minimum.reduce([lows, opens, closes])
    volumes = np.exp(rng.normal(15.0, 0.5, size=n_bars))
    timestamps = pd.date_range("2020-01-01", periods=n_bars, freq=bar)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=timestamps,
    )


def generate_volatility_cycle_ohlcv(
    n_bars: int = 4000,
    seed: int = 99,
    start_price: float = 30000.0,
    base_vol: float = 0.008,
    cycle_period: int = 240,
    cycle_amplitude: float = 4.0,
    bar: str = "1h",
) -> pd.DataFrame:
    """Vol-cycling synthetic series — long quiet periods punctuated by
    high-vol bursts. Useful for vol-breakout / squeeze strategies."""
    rng = np.random.default_rng(seed)
    n = n_bars
    t = np.arange(n)
    cycle = (np.sin(2 * np.pi * t / cycle_period) + 1.0) / 2.0  # 0..1
    # Long quiet stretches with brief eruptions
    vol_path = base_vol * (1.0 + cycle_amplitude * (cycle ** 4))
    log_p = np.zeros(n)
    log_p[0] = np.log(start_price)
    for i in range(1, n):
        log_p[i] = log_p[i - 1] + vol_path[i] * rng.normal()
    closes = np.exp(log_p)
    intra = rng.normal(0, base_vol * 0.5, size=n)
    highs = closes * (1.0 + np.abs(intra))
    lows = closes * (1.0 - np.abs(intra))
    opens = np.concatenate([[start_price], closes[:-1]])
    highs = np.maximum.reduce([highs, opens, closes])
    lows = np.minimum.reduce([lows, opens, closes])
    volumes = np.exp(rng.normal(15.0, 0.5, size=n))
    timestamps = pd.date_range("2020-01-01", periods=n, freq=bar)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=timestamps,
    )


def generate_correlated_panel(
    assets: list[str],
    n_bars: int = 4000,
    seed: int = 42,
    base_vol: float = 0.02,
    correlation: float = 0.6,
) -> dict[str, pd.DataFrame]:
    """Generate a correlated multi-asset panel for cross-sectional / pairs alphas."""
    rng = np.random.default_rng(seed)
    n = len(assets)
    # Common factor + idiosyncratic
    common = rng.normal(0, base_vol * np.sqrt(correlation), size=n_bars)
    out: dict[str, pd.DataFrame] = {}
    for i, asset in enumerate(assets):
        sub_seed = seed + i * 17
        df = generate_synthetic_ohlcv(
            n_bars=n_bars,
            seed=sub_seed,
            base_vol=base_vol,
            funding=False,
        )
        # Inject the common factor
        log_close = np.log(df["close"].values)
        for t in range(1, n_bars):
            log_close[t] += common[t]
        df["close"] = np.exp(log_close)
        # Re-derive OHLC from new close
        df["open"] = df["close"].shift(1).fillna(df["close"].iloc[0])
        df["high"] = df[["open", "close"]].max(axis=1) * 1.005
        df["low"] = df[["open", "close"]].min(axis=1) * 0.995
        out[asset] = df
    return out
