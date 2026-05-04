"""Standalone alpha validation used by the incubator and the walk-forward CLI.

Given an alpha factory + OHLCV, computes the summary stats that gate
promotion into the production ensemble:

  - sharpe_full     : per-bar Sharpe × sqrt(bars_per_year)
  - sharpe_oos      : same, but restricted to the last 20% holdout
  - max_drawdown    : worst peak-to-trough on the equity curve
  - ic              : Spearman IC (position vs forward return)
  - ic_ir           : mean(sub-window IC) / std(sub-window IC)
  - turnover        : mean |Δposition|

Cost model matches the live-trade assumptions:
  * taker_fee_bps       — charged per |Δposition|
  * slippage_bps        — same
  * funding_rate_hourly — charged on |position| every bar (1h bars)

The 8-year validation protocol the user enshrined as a feedback memory
bakes in as promotion gates: Sharpe_full ≥ 1.0 AND Sharpe_oos ≥ 0.7
AND |max_drawdown| ≤ 0.30 AND ic_ir ≥ 0.5. Callers decide the labels;
this module only reports the numbers.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig


BARS_PER_YEAR_1H = 24 * 365


@dataclass
class AlphaValidationReport:
    alpha_name: str
    params: dict
    asset: str
    n_bars: int
    sharpe_full: float
    sharpe_oos: float
    max_drawdown: float
    ic: float
    ic_ir: float
    turnover: float
    ann_return: float
    cost_drag: float
    regime_sharpe: dict[str, float] = field(default_factory=dict)
    gates: dict[str, bool] = field(default_factory=dict)

    def passes_promotion_gates(self) -> bool:
        return all(self.gates.values()) if self.gates else False

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────

OHLCV_DIR = Path(__file__).resolve().parents[2] / "data" / "ohlcv"


def load_ohlcv_stitched(asset: str, path: Path | None = None) -> pd.DataFrame:
    """Load an 8yr hourly OHLCV CSV into a DataFrame indexed by timestamp.

    Expects columns: timestamp, open, high, low, close, volume (extras ignored).
    """
    resolved = path or OHLCV_DIR / f"{asset}_1h_stitched.csv"
    if not resolved.exists():
        # Try the non-stitched form as a fallback so the validator still
        # runs for newer assets that only have a short history.
        resolved = resolved.with_name(f"{asset}_1h.csv")
    if not resolved.exists():
        raise FileNotFoundError(f"No OHLCV CSV found for {asset} at {resolved}")
    df = pd.read_csv(resolved)
    ts_col = "timestamp" if "timestamp" in df.columns else "open_time"
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col).sort_index()
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep].astype(float)


# ──────────────────────────────────────────────────────────────────
# Core stat computations
# ──────────────────────────────────────────────────────────────────


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 5:
        return 0.0
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return 0.0
    xr = pd.Series(x[mask]).rank().values
    yr = pd.Series(y[mask]).rank().values
    xr -= xr.mean()
    yr -= yr.mean()
    denom = math.sqrt((xr * xr).sum() * (yr * yr).sum())
    if denom == 0:
        return 0.0
    return float((xr * yr).sum() / denom)


def _max_drawdown(returns: np.ndarray) -> float:
    # Geometric equity curve: (1 + r1) * (1 + r2) * ... gives true compounded value
    equity = np.cumprod(1.0 + returns)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak  # fractional drawdown
    if len(dd) == 0:
        return 0.0
    return float(abs(dd.min()))


def _sharpe(returns: np.ndarray, bars_per_year: int = BARS_PER_YEAR_1H) -> float:
    if len(returns) < 2:
        return 0.0
    std = returns.std(ddof=1)
    if std == 0 or not np.isfinite(std):
        return 0.0
    return float(returns.mean() / std * math.sqrt(bars_per_year))


def _label_regime_from_row(row: pd.Series, atr_lookback: int = 24) -> str:
    # Minimal inline regime labeler (duplicates shared.regime to avoid
    # importing the full detector for one-off stats)
    adx = row.get("adx", 0.0) or 0.0
    atr_pct = row.get("atr_pct", 0.0) or 0.0
    if atr_pct >= 0.025:
        return "CRISIS"
    if adx >= 25:
        return "TREND_UP" if row.get("macd_hist", 0.0) >= 0 else "TREND_DOWN"
    return "RANGE"


def _enrich_with_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """Cheap on-the-fly regime labeling so backtest stats can be sliced
    per regime without spinning up the feature-store service."""
    out = df.copy()
    close = out["close"]
    out["ret_1h"] = close.pct_change().fillna(0.0)
    # Wilder-ish ADX approximation (close-only) — enough for coarse labeling.
    tr = out["high"].sub(out["low"]).abs()
    out["atr_pct"] = tr.rolling(14).mean() / close
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    macd = fast - slow
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = macd - macd_signal
    # Directional movement-based ADX (simplified)
    up = out["high"].diff().clip(lower=0)
    dn = (-out["low"].diff()).clip(lower=0)
    plus_di = 100 * (up.rolling(14).mean() / tr.rolling(14).mean().replace(0, np.nan))
    minus_di = 100 * (dn.rolling(14).mean() / tr.rolling(14).mean().replace(0, np.nan))
    dx = 100 * (plus_di.sub(minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    out["adx"] = dx.rolling(14).mean().fillna(0.0)
    out["regime"] = out.apply(_label_regime_from_row, axis=1)
    return out


# ──────────────────────────────────────────────────────────────────
# Validation entry point
# ──────────────────────────────────────────────────────────────────


def load_ohlcv_panel(assets: list[str]) -> dict[str, pd.DataFrame]:
    """Load several stitched OHLCV CSVs and align on a common timeline.

    Multi-asset alphas (StatArb, cross-sectional momentum) expect a dict
    panel; this helper keeps load logic in one place.
    """
    panel = {a: load_ohlcv_stitched(a) for a in assets}
    if not panel:
        return {}
    common = panel[assets[0]].index
    for df in panel.values():
        common = common.intersection(df.index)
    return {a: df.loc[common] for a, df in panel.items()}


def validate_alpha(
    alpha_factory: Callable[[AlphaConfig | None], Alpha],
    *,
    alpha_name: str,
    ohlcv: pd.DataFrame | dict[str, pd.DataFrame],
    asset: str,
    params: dict | None = None,
    taker_fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    # Binance perpetual funding is charged every 8h at ~0.01% per settlement;
    # amortized hourly that's ~1.25e-5. Using 1e-4 here would burn ~87%/yr
    # which swamps every alpha.
    funding_rate_hourly: float = 0.0000125,
    oos_fraction: float = 0.20,
    ic_subwindow: int = 500,
    promotion_gates: dict[str, float] | None = None,
) -> AlphaValidationReport:
    """Run *alpha_factory(config)* over *ohlcv* and score it.

    Costs are applied symmetrically: each bar pays
        cost = |Δposition| × (taker_fee_bps + slippage_bps) / 1e4
              + |position| × funding_rate_hourly
    which matches what order-service + exchange-adapter charge in live.
    """
    config = AlphaConfig(name=alpha_name, asset_type="crypto")
    if params:
        setattr(config, "params", params)  # pass-through for alpha subclasses
    alpha = alpha_factory(config)

    # Multi-asset alphas consume a {asset: df} panel; single-asset ones
    # take a DataFrame. When a panel is passed, we enrich the primary
    # asset with regime features and score the alpha's output against
    # that leg (its forward return is the canonical target here).
    if isinstance(ohlcv, dict):
        if asset not in ohlcv:
            raise ValueError(f"panel missing primary asset '{asset}'")
        primary_df = ohlcv[asset]
        enriched = _enrich_with_regime_features(primary_df).dropna()
        if len(enriched) < 1000:
            raise ValueError(f"OHLCV too short ({len(enriched)} bars) for meaningful stats")
        # Align panel to the enriched timeline so the alpha sees the
        # same windowed bars the scorer will grade.
        aligned_panel = {a: df.loc[df.index.intersection(enriched.index)] for a, df in ohlcv.items()}
        raw_signal = alpha.generate(aligned_panel)
    else:
        enriched = _enrich_with_regime_features(ohlcv).dropna()
        if len(enriched) < 1000:
            raise ValueError(f"OHLCV too short ({len(enriched)} bars) for meaningful stats")
        raw_signal = alpha.generate(enriched)

    pos = raw_signal.position.reindex(enriched.index).fillna(0.0).clip(-1.0, 1.0)

    # Target position for bar t becomes held from t+1 — Alpha base already
    # shifts by 1 so this Series is usable directly as held position.
    ret = enriched["ret_1h"].values
    position = pos.values
    d_pos = np.abs(np.diff(position, prepend=0.0))

    exec_cost_bps = (taker_fee_bps + slippage_bps) / 1e4
    trade_cost = d_pos * exec_cost_bps
    funding_cost = np.abs(position) * funding_rate_hourly
    gross_ret = position * ret
    net_ret = gross_ret - trade_cost - funding_cost

    # Forward return for IC — use the NEXT bar's realized return.
    fwd_ret = np.roll(ret, -1)
    fwd_ret[-1] = 0.0

    n = len(net_ret)
    split = int(n * (1.0 - oos_fraction))

    sharpe_full = _sharpe(net_ret)
    sharpe_oos = _sharpe(net_ret[split:])
    max_dd = _max_drawdown(net_ret)

    ic = _spearman(position, fwd_ret)
    sub_ics: list[float] = []
    for start in range(0, n - ic_subwindow + 1, ic_subwindow):
        sub_ics.append(_spearman(position[start:start + ic_subwindow], fwd_ret[start:start + ic_subwindow]))
    if len(sub_ics) >= 2:
        mean_ic = sum(sub_ics) / len(sub_ics)
        std_ic = math.sqrt(sum((x - mean_ic) ** 2 for x in sub_ics) / (len(sub_ics) - 1))
        ic_ir = (mean_ic / std_ic) if std_ic > 0 else 0.0
    else:
        ic_ir = 0.0

    turnover = float(d_pos.mean())
    ann_return = float(net_ret.sum() * BARS_PER_YEAR_1H / max(n, 1))
    cost_drag = float((trade_cost + funding_cost).sum() * BARS_PER_YEAR_1H / max(n, 1))

    # Per-regime Sharpe — equity behaviour in each market state.
    regime_sharpe: dict[str, float] = {}
    regime_labels = enriched["regime"].values
    for label in np.unique(regime_labels):
        mask = regime_labels == label
        if mask.sum() > 100:
            regime_sharpe[str(label)] = round(_sharpe(net_ret[mask]), 3)

    gates_cfg = promotion_gates or {
        "sharpe_full_min": 1.0,
        "sharpe_oos_min": 0.7,
        "max_drawdown_max": 0.30,
        "ic_ir_min": 0.5,
    }
    gates = {
        "sharpe_full": sharpe_full >= gates_cfg["sharpe_full_min"],
        "sharpe_oos": sharpe_oos >= gates_cfg["sharpe_oos_min"],
        "max_drawdown": max_dd <= gates_cfg["max_drawdown_max"],
        "ic_ir": abs(ic_ir) >= gates_cfg["ic_ir_min"],
    }

    return AlphaValidationReport(
        alpha_name=alpha_name,
        params=params or {},
        asset=asset,
        n_bars=n,
        sharpe_full=round(sharpe_full, 3),
        sharpe_oos=round(sharpe_oos, 3),
        max_drawdown=round(max_dd, 4),
        ic=round(ic, 4),
        ic_ir=round(ic_ir, 3),
        turnover=round(turnover, 5),
        ann_return=round(ann_return, 4),
        cost_drag=round(cost_drag, 5),
        regime_sharpe=regime_sharpe,
        gates=gates,
    )
