"""Alpha base class.

Conventions:
- Input: OHLCV DataFrame indexed by timestamp (or RangeIndex), columns at
  minimum {open, high, low, close, volume}. For multi-asset alphas the
  input is a dict[str, DataFrame].
- Output: AlphaSignal — a `position` series in [-1, 1] aligned to the input
  index, plus diagnostics. Position is interpreted as the *target* notional
  fraction at the close of bar `t`, applied at the next bar open.

Why a position-series contract instead of buy/sell events:
- Composes naturally with portfolio ensembles (sum of weighted positions).
- Avoids hidden state across calls.
- Works for both swing trading (slow position changes) and intra-bar exits
  (the backtest runner handles execution + stops, not the alpha).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class AlphaConfig:
    """Common alpha config knobs.

    Strategy-specific params live in `params`. The fields above are honored
    by the backtest runner and portfolio ensemble even if the alpha itself
    ignores them.
    """

    name: str
    asset_type: str = "crypto"
    # Position-sizing knobs (the runner reads these)
    max_gross_position: float = 1.0  # cap on |position| per bar
    long_only: bool = False
    # Risk budget knobs (ensemble reads these)
    target_vol: float = 0.20  # annualized, used for vol-targeting
    # Auto vol-target overlay (applied by base.generate() after smoothing).
    # OFF by default for backward compat: alphas that already call
    # vol_target_scale() inside _generate (e.g. vwap_reversion, order_flow,
    # kalman_trend, lead_lag, momentum_ensemble) would otherwise double-scale.
    # New alphas should set this True and skip manual vol-targeting.
    auto_vol_target: bool = False
    vol_target_lookback: int = 168          # bars (1 week on 1h)
    vol_target_periods_per_year: int = 24 * 365
    vol_target_cap: float = 1.5             # max scale-up multiplier
    vol_target_floor: float = 0.0           # min scale-down (0 = fully damp)
    # Free-form strategy-specific knobs
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlphaSignal:
    position: pd.Series  # index aligned to input; values in [-1, 1]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Hard-clamp position to [-1, 1] so misbehaving alphas can't break the runner
        self.position = self.position.astype(float).clip(-1.0, 1.0).fillna(0.0)


class Alpha:
    """Base class for all alphas.

    Subclasses implement `_generate(df)` returning a Series in [-1, 1].
    They MUST avoid look-ahead (no using future bars to set position[t]).
    The base class enforces this by shifting the raw output by 1 bar — the
    position decided using bar `t`'s close becomes the target *from* `t+1`.

    Class-level capability flags let the validator + meta-engine make
    informed decisions before calling generate():

      requires_panel   — needs a {asset: df} dict input, not a single
                         DataFrame. Cross-sectional and pairs alphas.
      requires_exog    — needs exogenous data injected via constructor
                         (fear/greed, funding, tradfi). Returning zero
                         from _generate() is the safe no-op when the
                         factory didn't wire it up.
      requires_training — ML alphas that need an explicit fit cycle
                         before predictions are meaningful. Default
                         False because most in-house ML alphas do
                         internal walk-forward training.
      safe_for_standalone_use — False when using this alpha solo as a
                         trading strategy is likely to cause catastrophic
                         cumulative drawdown. Consumers should only use
                         such alphas inside a diversified ensemble.
    """

    requires_panel: bool = False
    requires_exog: bool = False
    requires_training: bool = False
    safe_for_standalone_use: bool = True

    def __init__(self, config: AlphaConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    # ----- public API -----
    def generate(self, df: pd.DataFrame | dict[str, pd.DataFrame]) -> AlphaSignal:
        raw = self._generate(df)
        if not isinstance(raw, pd.Series):
            raise TypeError(
                f"{self.name}._generate must return pd.Series, got {type(raw).__name__}"
            )
        # Enforce no look-ahead: shift by 1 so target_pos[t+1] is decided on bar t close
        shifted = raw.shift(1).fillna(0.0)
        # Opt-in position smoothing: ML alphas and any alpha that sets
        # `position_smoothing` in its config params gets a final EMA
        # pass. Stops high-turnover predictors from bleeding all their
        # edge into transaction costs.
        smoothing = getattr(self, "_position_smoothing_default", 0)
        smoothing = int(self.config.params.get("position_smoothing", smoothing) or 0)
        if smoothing > 1:
            shifted = shifted.ewm(span=smoothing, adjust=False, min_periods=1).mean()
        # Apply long-only mask
        if self.config.long_only:
            shifted = shifted.clip(lower=0.0)
        # Auto vol-target overlay (opt-in). Skips panel inputs (multi-asset
        # dict) since each asset has its own vol — that's an ensemble-level
        # concern handled by EnsembleAllocator. Also skips when 'close' is
        # absent from the frame.
        vol_scale_last: float | None = None
        if self.config.auto_vol_target and isinstance(df, pd.DataFrame) and "close" in df.columns:
            scale = vol_target_scale(
                df["close"],
                target_vol_annual=self.config.target_vol,
                lookback=self.config.vol_target_lookback,
                periods_per_year=self.config.vol_target_periods_per_year,
                cap=self.config.vol_target_cap,
                floor=self.config.vol_target_floor,
            )
            # Align in case index has gaps; missing scale → 1.0 (neutral)
            scale = scale.reindex(shifted.index).fillna(1.0)
            shifted = shifted * scale
            vol_scale_last = float(scale.iloc[-1]) if len(scale) else None
        # Apply gross-position cap
        cap = self.config.max_gross_position
        shifted = shifted.clip(-cap, cap)
        diag = self._diagnostics(df, raw)
        if self.config.auto_vol_target:
            diag["vol_target_applied"] = True
            diag["vol_scale_last"] = vol_scale_last
        return AlphaSignal(position=shifted, diagnostics=diag)

    # ----- subclass hooks -----
    def _generate(self, df: pd.DataFrame | dict[str, pd.DataFrame]) -> pd.Series:
        raise NotImplementedError

    def _diagnostics(
        self,
        df: pd.DataFrame | dict[str, pd.DataFrame],
        raw_signal: pd.Series,
    ) -> dict[str, Any]:
        return {
            "alpha": self.name,
            "n_bars": int(len(raw_signal)),
            "long_pct": float((raw_signal > 0.05).mean()),
            "short_pct": float((raw_signal < -0.05).mean()),
            "flat_pct": float((raw_signal.abs() <= 0.05).mean()),
            "avg_abs_pos": float(raw_signal.abs().mean()),
        }


# ----- helpers shared by alphas -----
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    z = (series - mean) / std.replace(0, np.nan)
    return z.fillna(0.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def bollinger_pctb(close: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.Series:
    mean = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mean + n_std * std
    lower = mean - n_std * std
    width = (upper - lower).replace(0, np.nan)
    return ((close - lower) / width).fillna(0.5)


def vol_target_scale(
    close: pd.Series,
    target_vol_annual: float = 0.40,
    lookback: int = 168,
    periods_per_year: int = 24 * 365,
    cap: float = 1.5,
    floor: float = 0.0,
) -> pd.Series:
    """Per-bar vol-target multiplier in [floor, cap].

    multiplier_t = clip(target / realized_t, floor, cap)
    Realized vol = stdev of log returns over `lookback` bars × √periods_per_year.

    Used by individual alphas to dampen position when realized vol spikes,
    keeping per-alpha PnL contribution roughly proportional across regimes.
    """
    log_ret = np.log(close.astype(float) / close.astype(float).shift(1))
    rv = (log_ret.rolling(lookback, min_periods=24).std(ddof=0) * np.sqrt(periods_per_year))
    rv = rv.ffill().fillna(target_vol_annual).clip(lower=1e-6)
    return (target_vol_annual / rv).clip(lower=floor, upper=cap)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ADX. Returns a Series in [0, 100]."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = true_range(high, low, close)
    atr_v = tr.ewm(alpha=1.0 / period, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr_v.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr_v.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, min_periods=period).mean().fillna(0.0)
