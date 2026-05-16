"""Meta-ensemble combiner with MPT shrinkage, regime Kelly, and DD overlay.

Takes a collection of Alpha instances and combines their position series
into one target-position series that is ready to size and route to
order-service. Phases G/H/J from the research roadmap:

  G — MPT covariance-shrunk weights
      Alphas are treated as "assets". Rolling covariance of each
      alpha's 1-bar PnL (position × bar-return) is computed; weights
      come from a minimum-variance optimizer with Ledoit-Wolf
      shrinkage toward the diagonal. Purely long-only across alphas
      (no negative weights — we can't "short" a losing alpha).

  H — Drawdown-driven risk overlay
      Scales gross exposure down as realized drawdown grows. Full
      shutoff at kill_drawdown (emergency brake). Re-armed only when
      equity recovers past a sticky high-water threshold. Mirrors a
      conservative prop-desk drawdown rule.

  J — Regime-conditional Kelly sizing
      Per regime, estimate edge (mean alpha PnL) and vol (std alpha
      PnL). Kelly f* = edge / vol². Use half-Kelly (f*/2) capped at
      kelly_cap so sizing stays sub-optimal but safer. Turns off
      sizing when estimated edge is negative for a regime.

The module is pure function over (alpha_positions_df, bar_returns) —
no network, no Redis. Live consumers can pre-compute the weight tensor
from recent history and bind it to scoring.py at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────
# Phase G — MPT covariance-shrunk weights
# ──────────────────────────────────────────────────────────────────


def _ledoit_wolf_shrink(cov: np.ndarray, lam: float = 0.2) -> np.ndarray:
    """Shrink *cov* toward the identity-scaled diagonal.

    lam=0 → raw sample cov, lam=1 → diagonal only. lam=0.2 is a safe
    default when N_assets << N_obs.
    """
    if cov.size == 0:
        return cov
    mean_var = float(np.trace(cov) / max(cov.shape[0], 1))
    target = np.eye(cov.shape[0]) * mean_var
    return (1 - lam) * cov + lam * target


def sharpe_filtered_mv_weights(
    returns: pd.DataFrame,
    *,
    shrinkage: float = 0.3,
    long_only: bool = True,
    min_weight: float = 0.0,
    max_weight: float = 0.6,
    min_sharpe: float = 0.0,
    min_samples: int = 50,
) -> pd.Series:
    """Min-variance weights restricted to alphas with positive edge.

    Plain min-variance is risk-agnostic about expected return — it
    happily allocates to a steadily losing alpha if its variance is
    low. Not what we want in a production meta-ensemble. This
    variant:

      1. Drops alphas whose per-bar Sharpe falls below *min_sharpe*.
      2. Falls back to equal-weight over survivors if MV fails or
         all alphas get filtered out.
      3. Applies Ledoit-Wolf shrinkage + iterative long-only clipping
         as before.

    *returns* is an N_bars × N_alphas DataFrame of per-alpha PnL.
    """
    cols_all = list(returns.columns)
    if not cols_all:
        return pd.Series(dtype=float)

    # Filter: keep alphas with enough observations and positive Sharpe.
    survivors: list[str] = []
    for c in cols_all:
        s = returns[c].dropna()
        if len(s) < min_samples:
            continue
        std = float(s.std(ddof=1))
        if std <= 0:
            continue
        sharpe = float(s.mean() / std)
        if sharpe >= min_sharpe:
            survivors.append(c)

    # Fallback — if filter kills everyone, spread evenly over the
    # least-bad ones. Keeps the ensemble from silently zeroing out.
    if not survivors:
        eq = 1.0 / max(len(cols_all), 1)
        return pd.Series({c: eq for c in cols_all})

    mat = returns[survivors].dropna().to_numpy()
    if mat.shape[0] < 30 or mat.shape[1] == 0:
        eq = 1.0 / len(survivors)
        out = {c: (eq if c in survivors else 0.0) for c in cols_all}
        return pd.Series(out)

    cov = np.cov(mat, rowvar=False)
    cov = _ledoit_wolf_shrink(np.atleast_2d(cov), shrinkage)
    try:
        inv = np.linalg.pinv(cov)
    except Exception:
        inv = np.eye(cov.shape[0])
    ones = np.ones(cov.shape[0])
    raw = inv @ ones
    denom = ones @ raw
    w = raw / denom if denom != 0 else ones / len(survivors)

    if long_only:
        for _ in range(10):
            w = np.clip(w, min_weight, max_weight)
            s = w.sum()
            if s <= 0:
                w = np.ones_like(w) / len(w)
                break
            w = w / s
            if (w >= min_weight - 1e-9).all() and (w <= max_weight + 1e-9).all():
                break

    out = {c: 0.0 for c in cols_all}
    out.update(dict(zip(survivors, w.tolist())))
    return pd.Series(out)


# Backwards-compat alias — older callers still import min_variance_weights.
min_variance_weights = sharpe_filtered_mv_weights


# ──────────────────────────────────────────────────────────────────
# Phase H — Drawdown overlay
# ──────────────────────────────────────────────────────────────────


def drawdown_overlay(
    equity: pd.Series,
    *,
    warn_dd: float = 0.10,
    cut_dd: float = 0.20,
    kill_dd: float = 0.30,
    recovery_margin: float = 0.02,
) -> pd.Series:
    """Return a [0, 1] multiplier series from realized drawdown.

    * |DD| < warn_dd      → 1.0 (no damping)
    * warn_dd ≤ |DD| < cut_dd → linearly damp 1.0 → 0.5
    * cut_dd ≤ |DD| < kill_dd → damp 0.5 → 0.0
    * |DD| ≥ kill_dd      → 0.0 (flat) until equity recovers past
                              (peak − recovery_margin).
    """
    if equity.empty:
        return equity
    peak = equity.cummax()
    dd = (peak - equity) / peak.replace(0, np.nan)
    dd = dd.fillna(0.0).clip(lower=0.0)

    mult = np.where(
        dd < warn_dd,
        1.0,
        np.where(
            dd < cut_dd,
            1.0 - 0.5 * (dd - warn_dd) / max(cut_dd - warn_dd, 1e-9),
            np.where(
                dd < kill_dd,
                0.5 - 0.5 * (dd - cut_dd) / max(kill_dd - cut_dd, 1e-9),
                0.0,
            ),
        ),
    )
    mult = pd.Series(mult, index=equity.index).clip(0.0, 1.0)

    # Sticky kill: once we hit kill_dd, stay at 0 until equity
    # recovers within recovery_margin of the prior peak. Prevents
    # bouncing right back into a losing streak on the first green bar.
    killed = False
    out = mult.copy()
    arr = out.to_numpy()
    dd_arr = dd.to_numpy()
    peak_arr = peak.to_numpy()
    eq_arr = equity.to_numpy()
    for i in range(len(arr)):
        if dd_arr[i] >= kill_dd:
            killed = True
        if killed:
            arr[i] = 0.0
            if eq_arr[i] >= peak_arr[i] * (1.0 - recovery_margin):
                killed = False
    return pd.Series(arr, index=equity.index)


# ──────────────────────────────────────────────────────────────────
# Phase G' — Regime-conditional MV weights
# ──────────────────────────────────────────────────────────────────


def compute_regime_alpha_weights(
    pnl_panel: pd.DataFrame,
    regime: pd.Series,
    *,
    min_regime_samples: int = 200,
    fallback_to_global: bool = True,
    shrinkage: float = 0.3,
    min_weight: float = 0.0,
    max_weight: float = 0.6,
    min_sharpe: float = 0.0,
) -> dict[str, pd.Series]:
    """Per-regime alpha weights via MV optimization on regime-sliced PnL.

    Returns {regime_label: weights_series indexed by pnl_panel.columns}.

    For each regime with ≥ min_regime_samples bars, runs the standard
    sharpe-filtered MV optimization on PnL restricted to that regime —
    so an alpha that's a star in TREND_UP and a dog in RANGE gets a
    high weight only in trending bars.

    When a regime has too few samples and fallback_to_global is True,
    that regime gets the global (pooled) MV weight instead of zero —
    avoids zeroing out warm-up periods or rare regimes.
    """
    out: dict[str, pd.Series] = {}
    if pnl_panel.empty:
        return out
    regime_aligned = regime.reindex(pnl_panel.index).fillna("unknown").astype(str)
    global_w = None
    if fallback_to_global:
        global_w = sharpe_filtered_mv_weights(
            pnl_panel,
            shrinkage=shrinkage,
            long_only=True,
            min_weight=min_weight,
            max_weight=max_weight,
            min_sharpe=min_sharpe,
        )
    for label in regime_aligned.unique():
        mask = regime_aligned == label
        regime_pnl = pnl_panel.loc[mask]
        if len(regime_pnl) < min_regime_samples:
            if global_w is not None:
                out[str(label)] = global_w.copy()
            continue
        out[str(label)] = sharpe_filtered_mv_weights(
            regime_pnl,
            shrinkage=shrinkage,
            long_only=True,
            min_weight=min_weight,
            max_weight=max_weight,
            min_sharpe=min_sharpe,
        )
    # Catch-all 'unknown' for bars whose regime never appeared in the table.
    if "unknown" not in out and global_w is not None:
        out["unknown"] = global_w.copy()
    return out


def expand_regime_weights_to_panel(
    regime_weights: dict[str, pd.Series],
    regime: pd.Series,
    alpha_columns: list[str],
) -> pd.DataFrame:
    """Tile per-regime weight vectors out to a per-bar weight matrix.

    Output shape: len(regime) × len(alpha_columns). Each bar gets the
    weight vector of its regime label. Bars whose regime has no entry
    fall through to 'unknown' (if present) or zeros.
    """
    fallback = regime_weights.get("unknown")
    rows = []
    for ts, label in regime.items():
        w = regime_weights.get(str(label), fallback)
        if w is None:
            rows.append(pd.Series(0.0, index=alpha_columns))
        else:
            rows.append(w.reindex(alpha_columns).fillna(0.0))
    out = pd.DataFrame(rows, index=regime.index)
    out.columns = alpha_columns
    return out


# ──────────────────────────────────────────────────────────────────
# Phase J — Regime-conditional Kelly sizing
# ──────────────────────────────────────────────────────────────────


@dataclass
class KellyRegimeTable:
    """Per-regime Kelly fraction derived from historical alpha PnL."""
    fractions: dict[str, float] = field(default_factory=dict)
    samples: dict[str, int] = field(default_factory=dict)

    def get(self, regime: str, default: float = 0.0) -> float:
        return self.fractions.get(regime, default)


def compute_regime_kelly(
    alpha_pnl: pd.Series,
    regime: pd.Series,
    *,
    min_samples: int = 100,
    half_kelly: bool = True,
    kelly_cap: float = 0.5,
) -> KellyRegimeTable:
    """Estimate a Kelly fraction per regime.

    f* = mean / variance. For each regime with ≥ min_samples, compute
    f*, optionally halve (safer in practice — see Vince 1992, Thorp
    1975), cap at kelly_cap, and floor at 0 when edge is non-positive
    (no sizing into losing regimes).
    """
    table = KellyRegimeTable()
    if alpha_pnl.empty:
        return table
    regime = regime.reindex(alpha_pnl.index).fillna("unknown")
    for label in regime.unique():
        mask = regime == label
        pnl = alpha_pnl[mask].dropna()
        if len(pnl) < min_samples:
            continue
        mu = float(pnl.mean())
        var = float(pnl.var(ddof=1))
        if var <= 0 or mu <= 0:
            table.fractions[str(label)] = 0.0
            table.samples[str(label)] = len(pnl)
            continue
        kelly = mu / var
        if half_kelly:
            kelly = kelly / 2.0
        table.fractions[str(label)] = float(np.clip(kelly, 0.0, kelly_cap))
        table.samples[str(label)] = len(pnl)
    return table


# ──────────────────────────────────────────────────────────────────
# Main combiner
# ──────────────────────────────────────────────────────────────────


@dataclass
class MetaEnsembleConfig:
    # Phase G
    mv_shrinkage: float = 0.3
    mv_min_weight: float = 0.0
    mv_max_weight: float = 0.6
    min_alpha_sharpe: float = 0.0  # drop losing alphas before MV weighting
    # Phase G' — Regime-conditional MV weights (opt-in)
    use_regime_conditional_weights: bool = False
    regime_min_samples: int = 200
    # Phase H
    warn_dd: float = 0.10
    cut_dd: float = 0.20
    kill_dd: float = 0.30
    recovery_margin: float = 0.02
    # Phase J
    kelly_cap: float = 0.5
    kelly_half: bool = True
    kelly_min_samples: int = 100
    # Global
    gross_cap: float = 1.0


def combine(
    alpha_positions: pd.DataFrame,
    bar_returns: pd.Series,
    *,
    regime: Optional[pd.Series] = None,
    config: MetaEnsembleConfig | None = None,
) -> dict:
    """Combine an alpha-positions panel into a single target position.

    Parameters
    ----------
    alpha_positions : DataFrame
        Index = timestamps, columns = alpha names, values in [-1, 1].
    bar_returns : Series
        Realized bar-to-bar return (log or simple) of the underlying.
    regime : Series, optional
        Categorical regime label per bar. Drives Kelly table slicing
        in Phase J. Pass None to skip regime-conditional sizing.
    config : MetaEnsembleConfig

    Returns
    -------
    dict with keys:
      position            — combined target position in [-gross_cap, gross_cap]
      raw_combined        — position before DD / Kelly scaling
      alpha_weights       — MV weights per alpha
      dd_multiplier       — overlay series in [0, 1]
      kelly_table         — {regime: fraction}
      alpha_pnl_panel     — per-alpha per-bar PnL (for audit)
    """
    cfg = config or MetaEnsembleConfig()

    # Per-alpha PnL: position × bar_return (position is already shifted
    # internally by the Alpha base class, so no look-ahead here).
    pnl_panel = alpha_positions.mul(bar_returns, axis=0).fillna(0.0)

    # Global (pooled) MV weights — always computed, used as fallback and
    # also returned in the result for auditability.
    weights = sharpe_filtered_mv_weights(
        pnl_panel,
        shrinkage=cfg.mv_shrinkage,
        long_only=True,
        min_weight=cfg.mv_min_weight,
        max_weight=cfg.mv_max_weight,
        min_sharpe=getattr(cfg, "min_alpha_sharpe", 0.0),
    )
    weights = weights.reindex(alpha_positions.columns).fillna(0.0)

    # Phase G': regime-conditional weights. Each bar gets the MV weight
    # vector estimated on its regime's PnL slice — turns the meta-ensemble
    # into a true regime rotation. Falls back to pooled weights when a
    # regime is under-sampled, and to global behavior when disabled or
    # when no regime series is provided.
    regime_weights_table: dict[str, dict[str, float]] = {}
    if cfg.use_regime_conditional_weights and regime is not None and not regime.empty:
        regime_w = compute_regime_alpha_weights(
            pnl_panel,
            regime,
            min_regime_samples=cfg.regime_min_samples,
            fallback_to_global=True,
            shrinkage=cfg.mv_shrinkage,
            min_weight=cfg.mv_min_weight,
            max_weight=cfg.mv_max_weight,
            min_sharpe=getattr(cfg, "min_alpha_sharpe", 0.0),
        )
        regime_weights_table = {k: v.to_dict() for k, v in regime_w.items()}
        weight_panel = expand_regime_weights_to_panel(
            regime_w,
            regime.reindex(alpha_positions.index).fillna("unknown").astype(str),
            list(alpha_positions.columns),
        )
        raw_combined = (alpha_positions * weight_panel).sum(axis=1).fillna(0.0)
    else:
        # Combined position is the weight-dot-product of alpha positions.
        raw_combined = alpha_positions.mul(weights, axis=1).sum(axis=1).fillna(0.0)

    # Combined PnL for DD overlay + Kelly table.
    combined_pnl = raw_combined * bar_returns
    equity = combined_pnl.cumsum()
    equity = (equity - equity.min() + 1.0) if equity.min() < 0 else (equity + 1.0)

    dd_mult = drawdown_overlay(
        equity,
        warn_dd=cfg.warn_dd,
        cut_dd=cfg.cut_dd,
        kill_dd=cfg.kill_dd,
        recovery_margin=cfg.recovery_margin,
    )

    if regime is not None and not regime.empty:
        kelly_table = compute_regime_kelly(
            combined_pnl,
            regime.reindex(combined_pnl.index).fillna("unknown"),
            min_samples=cfg.kelly_min_samples,
            half_kelly=cfg.kelly_half,
            kelly_cap=cfg.kelly_cap,
        )
        regime_scale = regime.reindex(raw_combined.index).fillna("unknown").map(
            lambda r: kelly_table.fractions.get(str(r), 0.0)
        ).astype(float)
        # Keep a reasonable floor so a warmup regime (under-sampled) doesn't
        # silently zero out every bar — at worst, fall back to half-kelly
        # of the pooled edge.
        if regime_scale.sum() == 0:
            regime_scale = pd.Series(cfg.kelly_cap / 2, index=raw_combined.index)
    else:
        kelly_table = KellyRegimeTable()
        regime_scale = pd.Series(1.0, index=raw_combined.index)

    scaled = raw_combined * dd_mult * regime_scale
    position = scaled.clip(-cfg.gross_cap, cfg.gross_cap)

    return {
        "position": position,
        "raw_combined": raw_combined,
        "alpha_weights": weights.to_dict(),
        "regime_alpha_weights": regime_weights_table,  # {} when disabled
        "dd_multiplier": dd_mult,
        "kelly_table": kelly_table.fractions,
        "alpha_pnl_panel": pnl_panel,
    }
