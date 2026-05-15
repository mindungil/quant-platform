"""Funding rate carry alpha.

Exploits the perpetual swap funding rate as a carry signal. Funding is
settled every 8 hours on Binance; when the rate is positive, longs pay
shorts, and vice versa. Extreme positive funding → crowded longs →
short signal (you get PAID to short). Extreme negative → short signal
(you get paid to long).

This alpha is fundamentally different from price-based alphas:
  - Signal comes from a DIFFERENT data source (funding rates, not OHLCV)
  - Very low turnover (funding rates are sticky, change slowly)
  - Natural hedge against trend-following (carry is often contrarian)

Implementation:
  1) Load funding rate data from data/funding/{symbol}_funding.csv
  2) Resample to 1h (forward-fill from the last 8h settlement)
  3) Z-score against rolling 30-day mean
  4) Signal = -z (fade the extreme: high funding → short, low → long)
  5) Apply dead zone (|z| < 1.0 → flat) to reduce turnover
  6) Scale by tanh for position sizing

Reference: Hautsch & Horváth (2023) "Funding Rate Arbitrage in Crypto"
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal, ema

FUNDING_DIR = Path(__file__).resolve().parents[1].parent / "data" / "funding"


class FundingCarryAlpha(Alpha):
    def __init__(self, config: AlphaConfig | None = None, symbol: str = "BTCUSDT") -> None:
        super().__init__(config or AlphaConfig(name="funding_carry"))
        self._symbol = symbol

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        p = self.config.params
        z_window = int(p.get("z_window", 720))  # 30 days at 1h
        dead_zone = float(p.get("dead_zone", 1.0))
        smooth = int(p.get("smooth", 8))
        scale = float(p.get("scale", 0.8))

        close = df["close"].astype(float)
        idx = df.index

        # Load funding data
        funding = self._load_funding(idx)
        if funding.abs().sum() < 1e-12:
            return pd.Series(0.0, index=idx)

        # Z-score of funding rate
        mean = funding.rolling(z_window, min_periods=z_window // 3).mean()
        std = funding.rolling(z_window, min_periods=z_window // 3).std(ddof=0).replace(0, np.nan)
        z = ((funding - mean) / std).fillna(0.0)

        # Fade: positive funding z → short (crowded longs paying up)
        signal = -z

        # Dead zone: only act on extreme funding
        signal = signal.where(z.abs() > dead_zone, 0.0)

        # Smooth
        signal = ema(signal, smooth) * scale

        # Tanh compression
        return np.tanh(signal)

    def _load_funding(self, target_index: pd.DatetimeIndex) -> pd.Series:
        """Load and align funding rate to target index."""
        path = FUNDING_DIR / f"{self._symbol}_funding.csv"
        if not path.exists():
            return pd.Series(0.0, index=target_index)
        try:
            fdf = pd.read_csv(path, index_col=0, parse_dates=True)
            fdf.index = pd.to_datetime(fdf.index, format="mixed", utc=True)
            fdf["fundingRate"] = pd.to_numeric(fdf["fundingRate"], errors="coerce")
            # Resample 8h → 1h by forward fill, then divide by 8 for hourly rate
            hourly = fdf["fundingRate"].resample("1h").ffill().fillna(0.0) / 8.0
            # Align to target index
            aligned = hourly.reindex(target_index).ffill().fillna(0.0)
            return aligned
        except Exception:
            return pd.Series(0.0, index=target_index)
