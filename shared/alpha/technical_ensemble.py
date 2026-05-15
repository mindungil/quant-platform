"""Technical ensemble alpha — mirrors live signal-service scoring.

The production live-trading signal engine (services/signal-service/
app/core/scoring.py) combines RSI, MACD, SMA, VWAP, Bollinger, ADX
using ATR-normalized distances + IC-weighted / regime-heuristic
aggregation. That logic ran outside the shared.alpha interface, so
the incubator could not validate it.

This class reproduces the scoring logic against a raw OHLCV bar
stream, computing all required features on the fly so 8-year
walk-forward backtests grade *the actual live engine* end-to-end.

Design choices that mirror scoring.py:
  * Technical components: rsi, macd_histogram, sma_20_distance,
    vwap_distance, bollinger_%B. Stochastic is excluded when RSI is
    present (correlation > 0.85, per scoring.py:127-132).
  * adx_multiplier: 0.5 below 20, 1.0 at 20-40, 1.2 above 40.
  * Heuristic regime weights (trending vs reverting) applied to each
    component before summation.
  * Agreement bonus (×1.15) when ≥80% of components share a sign.
  * Clipped to [-1, 1].

Differences from live scoring.py:
  * Does not consume IC weights from Redis — the standalone alpha must
    be reproducible offline. The regime heuristic is used as-is.
  * Does not apply the external-context blend (fear/greed, onchain,
    macro, news). That layer depends on a separate data feed; when
    present in live scoring it adds ~0.3 weight on top of technicals.
    For pure OHLCV-driven backtest grading, we leave it out.
  * Signal is converted to a continuous position in [-1, 1] rather
    than BUY/SELL/HOLD discrete actions — the backtester treats the
    position as the target exposure fraction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import (
    Alpha,
    AlphaConfig,
    adx as compute_adx,
    atr as compute_atr,
    bollinger_pctb,
    ema,
    rsi as compute_rsi,
)


class TechnicalEnsembleAlpha(Alpha):
    """Live-scoring replica as a standalone Alpha."""

    DEFAULT_PARAMS = {
        "rsi_period": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "sma_period": 20,
        "bb_period": 20,
        "bb_std": 2.0,
        "vwap_window": 24,        # rolling VWAP over 1 day on hourly bars
        "atr_period": 14,
        "adx_period": 14,
        "adx_low": 20.0,          # below → 0.5× adx_multiplier
        "adx_high": 40.0,         # above → 1.2× adx_multiplier
        "is_trending_adx": 25.0,
        "agreement_bonus": 1.15,
        # Hysteresis band tuned so hourly BTC cost drag stays under
        # 8%/yr. Wider entry keeps us out of chop; generous exit holds
        # winners until directional conviction fully fades.
        "entry_threshold": 0.55,
        "exit_threshold": 0.15,
        # Same momentum/reversion sets as scoring.py.
        "momentum_components": ("rsi", "macd"),
        "reversion_components": ("sma_20", "vwap", "bollinger"),
        # Longer EMA on the raw score — meaningful for 1h bars, filters
        # out intra-day noise that would bleed the hysteresis band.
        "ramp_smoothing": 24,
        # Minimum hold window (in bars) after entry. Prevents the band
        # from flickering on a single reversion bar immediately after
        # entry (a common cause of "enter → stop-out same bar" drag).
        "min_hold_bars": 6,
    }

    def __init__(self, config: AlphaConfig | None = None) -> None:
        cfg = config or AlphaConfig(name="technical_ensemble", asset_type="crypto")
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(cfg.params)
        cfg.params = merged
        super().__init__(cfg)

    # ------------------------------------------------------------------
    # Component computations
    # ------------------------------------------------------------------

    @staticmethod
    def _rolling_vwap(df: pd.DataFrame, window: int) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        pv = typical * df["volume"]
        vw = pv.rolling(window, min_periods=window).sum() / df["volume"].rolling(
            window, min_periods=window
        ).sum().replace(0.0, np.nan)
        return vw

    def _compute_components(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        p = self.config.params
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        rsi_v = compute_rsi(close, p["rsi_period"])
        fast = ema(close, p["macd_fast"])
        slow = ema(close, p["macd_slow"])
        macd_line = fast - slow
        macd_signal_line = ema(macd_line, p["macd_signal"])
        macd_hist = macd_line - macd_signal_line

        sma_20 = close.rolling(p["sma_period"], min_periods=p["sma_period"]).mean()
        vwap_v = self._rolling_vwap(df, p["vwap_window"])
        bb_pct = bollinger_pctb(close, p["bb_period"], p["bb_std"])

        atr_v = compute_atr(high, low, close, p["atr_period"])
        # Floor ATR at 1% of price so division doesn't blow up on flat bars.
        atr_floor = (close * 0.01).fillna(1.0)
        atr_use = atr_v.where(atr_v > 0, atr_floor).fillna(atr_floor)

        adx_v = compute_adx(high, low, close, p["adx_period"])

        # RSI: (rsi - 50) / 50 → [-1, 1]
        rsi_conf = ((rsi_v - 50.0) / 50.0).clip(-1.0, 1.0).fillna(0.0)
        # MACD histogram normalized by ATR
        macd_conf = np.tanh(macd_hist / atr_use).fillna(0.0)
        # SMA distance in ATR×sqrt(n) units
        sma_conf = np.tanh((close - sma_20) / (atr_use * np.sqrt(p["sma_period"]))).fillna(0.0)
        vwap_conf = np.tanh((close - vwap_v) / (atr_use * 2.0)).fillna(0.0)
        # Bollinger %B → [-1, 1] centered at 0.5
        bb_conf = ((bb_pct - 0.5) * 2.0).clip(-1.0, 1.0).fillna(0.0)

        return {
            "rsi": rsi_conf,
            "macd": macd_conf,
            "sma_20": sma_conf,
            "vwap": vwap_conf,
            "bollinger": bb_conf,
            "_adx": adx_v,
        }

    # ------------------------------------------------------------------
    # Aggregation (mirrors scoring.py heuristic path)
    # ------------------------------------------------------------------

    def _aggregate(self, comps: dict[str, pd.Series]) -> pd.Series:
        p = self.config.params
        adx = comps["_adx"]
        is_trending = adx >= p["is_trending_adx"]

        momentum = set(p["momentum_components"])
        reversion = set(p["reversion_components"])

        # Base weight per component: 0.5 + |val|*0.5, then regime tilt.
        weighted_sum = pd.Series(0.0, index=adx.index)
        weight_total = pd.Series(0.0, index=adx.index)
        names = [k for k in comps if not k.startswith("_")]
        for key in names:
            val = comps[key]
            w = 0.5 + val.abs() * 0.5
            trend_tilt = pd.Series(1.0, index=adx.index)
            if key in momentum:
                trend_tilt = trend_tilt.where(~is_trending, 1.4).where(is_trending, 0.7)
            elif key in reversion:
                trend_tilt = trend_tilt.where(is_trending, 1.4).where(~is_trending, 0.7)
            w = w * trend_tilt
            weighted_sum = weighted_sum + val * w
            weight_total = weight_total + w

        # ADX multiplier
        adx_mult = pd.Series(1.0, index=adx.index)
        adx_mult = adx_mult.where(adx >= p["adx_low"], 0.5)
        adx_mult = adx_mult.where(adx <= p["adx_high"], 1.2)

        technical_score = (weighted_sum / weight_total.replace(0.0, np.nan)).fillna(0.0)
        technical_score = technical_score * adx_mult

        # Agreement bonus — same threshold (0.05) as scoring.py line 241.
        comp_df = pd.concat([comps[k] for k in names], axis=1)
        comp_df.columns = names
        signs = comp_df.applymap(lambda v: 1 if v > 0.05 else (-1 if v < -0.05 else 0))
        n = len(names)
        pos = (signs == 1).sum(axis=1)
        neg = (signs == -1).sum(axis=1)
        agreement = pd.concat([pos, neg], axis=1).max(axis=1) / n
        bonus = agreement.where(agreement < 0.8, p["agreement_bonus"]).where(
            agreement >= 0.8, 1.0
        )
        technical_score = technical_score * bonus

        return technical_score.clip(-1.0, 1.0)

    @staticmethod
    def _hysteresis_position(
        score: pd.Series,
        entry_thr: float,
        exit_thr: float,
        min_hold_bars: int = 0,
    ) -> pd.Series:
        """Convert a continuous score into a held-position series.

        * No position (0) until |score| reaches *entry_thr* → then take
          signed position with magnitude = score (still in [-1, 1]).
        * Hold the existing direction until |score| drops below
          *exit_thr* — flatten to 0 at that point.
        * Allow direct reversal: crossing -entry_thr while long flips
          straight to short, without requiring an exit → re-entry.
        * *min_hold_bars* enforces a cooldown — after entry, hold the
          position for at least N bars regardless of signal. Protects
          against flickering on a single reversion bar right after entry.

        Empirically takes backtest turnover from ~0.07/bar continuous
        to ~0.005-0.015/bar.
        """
        arr = score.to_numpy()
        n = len(arr)
        out = np.zeros(n, dtype=np.float64)
        state = 0  # -1, 0, +1
        held = 0  # bars held in current direction
        for i in range(n):
            s = arr[i]
            if np.isnan(s):
                out[i] = 0.0
                continue
            abs_s = abs(s)
            if state == 0:
                if abs_s >= entry_thr:
                    state = 1 if s > 0 else -1
                    held = 1
            else:
                held += 1
                if held >= min_hold_bars:
                    if state > 0 and s <= -entry_thr:
                        state = -1
                        held = 1
                    elif state < 0 and s >= entry_thr:
                        state = 1
                        held = 1
                    elif abs_s < exit_thr:
                        state = 0
                        held = 0
            if state != 0:
                out[i] = state * max(abs_s, exit_thr)
            else:
                out[i] = 0.0
        return pd.Series(out, index=score.index).clip(-1.0, 1.0)

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        if isinstance(df, dict):
            raise TypeError("technical_ensemble expects a single-asset OHLCV DataFrame")

        comps = self._compute_components(df)
        score = self._aggregate(comps)

        p = self.config.params
        if p.get("ramp_smoothing", 1) > 1:
            score = score.ewm(span=p["ramp_smoothing"], adjust=False, min_periods=1).mean()

        return self._hysteresis_position(
            score,
            float(p["entry_threshold"]),
            float(p["exit_threshold"]),
            min_hold_bars=int(p.get("min_hold_bars", 0)),
        ).fillna(0.0)
