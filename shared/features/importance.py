"""Feature importance tracking and IC-based dynamic feature selection.

Computes rolling Information Coefficient (Spearman rank correlation between
feature values and forward returns) for each feature. Tracks which features
are persistently informative vs noisy, enabling dynamic feature pruning
for ML alphas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger("feature-importance")


@dataclass
class FeatureImportanceReport:
    """Summary of feature informativeness over a lookback window."""
    ic_mean: dict[str, float]          # feature → mean IC
    ic_std: dict[str, float]           # feature → IC volatility
    ic_ir: dict[str, float]            # feature → IC information ratio (mean/std)
    stable_features: list[str]         # features with IC_IR > threshold
    top_features: list[str]            # top-N by IC_IR
    n_features_total: int
    n_features_stable: int
    lookback_bars: int


def compute_rolling_ic(
    features: pd.DataFrame,
    forward_returns: pd.Series,
    window: int = 500,
    method: str = "spearman",
) -> pd.DataFrame:
    """Compute rolling IC for each feature vs forward returns.

    Args:
        features: (n_bars, n_features) DataFrame
        forward_returns: bar-ahead log return, aligned to features index
        window: rolling window for IC computation
        method: correlation method ("spearman" or "pearson")

    Returns:
        DataFrame of rolling IC per feature, same index as features.
    """
    # Align
    fr = forward_returns.reindex(features.index).fillna(0.0)

    ic_dict: dict[str, pd.Series] = {}
    for col in features.columns:
        feat = features[col]
        if method == "spearman":
            ic = feat.rolling(window, min_periods=window // 2).corr(fr)
        else:
            ic = feat.rolling(window, min_periods=window // 2).corr(fr)
        ic_dict[col] = ic.fillna(0.0)

    return pd.DataFrame(ic_dict, index=features.index)


def rank_features(
    ic_panel: pd.DataFrame,
    *,
    min_ic_mean: float = 0.02,
    min_ic_ir: float = 0.3,
    top_n: int = 50,
    lookback: int | None = None,
) -> FeatureImportanceReport:
    """Rank features by IC Information Ratio and select top-N stable ones.

    IC_IR = mean(IC) / std(IC) — measures signal consistency, not just strength.
    A feature with IC_IR > 0.3 has a consistently positive information edge.

    Args:
        ic_panel: output of compute_rolling_ic
        min_ic_mean: minimum average IC to be considered
        min_ic_ir: minimum IC_IR for stability filter
        top_n: maximum features to select
        lookback: if set, only use last N bars of IC panel
    """
    if lookback and lookback < len(ic_panel):
        panel = ic_panel.iloc[-lookback:]
    else:
        panel = ic_panel

    ic_mean = panel.mean().to_dict()
    ic_std = panel.std().to_dict()

    ic_ir = {}
    for col in panel.columns:
        std = ic_std.get(col, 0)
        mean = ic_mean.get(col, 0)
        ic_ir[col] = mean / std if std > 1e-9 else 0.0

    # Stability filter: positive mean IC + sufficient IR
    stable = [
        col for col in panel.columns
        if abs(ic_mean.get(col, 0)) >= min_ic_mean and ic_ir.get(col, 0) >= min_ic_ir
    ]

    # Sort by IC_IR descending, take top-N
    sorted_features = sorted(
        stable,
        key=lambda c: ic_ir.get(c, 0),
        reverse=True,
    )
    top = sorted_features[:top_n]

    # If not enough stable features, fill with best by absolute IC
    if len(top) < min(top_n, len(panel.columns)):
        remaining = [c for c in panel.columns if c not in top]
        remaining.sort(key=lambda c: abs(ic_mean.get(c, 0)), reverse=True)
        for c in remaining:
            if len(top) >= top_n:
                break
            top.append(c)

    return FeatureImportanceReport(
        ic_mean={k: round(v, 5) for k, v in ic_mean.items()},
        ic_std={k: round(v, 5) for k, v in ic_std.items()},
        ic_ir={k: round(v, 4) for k, v in ic_ir.items()},
        stable_features=sorted(stable),
        top_features=top,
        n_features_total=len(panel.columns),
        n_features_stable=len(stable),
        lookback_bars=len(panel),
    )


def robust_feature_ranking(
    features: pd.DataFrame,
    forward_returns: pd.Series,
    *,
    ic_windows: list[int] | None = None,
    n_bootstrap: int = 10,
    bootstrap_frac: float = 0.7,
    seed: int = 42,
) -> pd.DataFrame:
    """Robust feature ranking via multi-window IC + bootstrap stability.

    A feature's IC_IR can look strong in a single rolling window but
    collapse in another — either from regime change or overfitting
    the specific window length. This function:

      1. Computes IC over multiple windows (short/med/long).
      2. Bootstraps each window's IC panel (sample 70% of bars).
      3. Reports features whose rank is stable across both dimensions.

    A feature is "robust" when it:
      - Has consistently positive (or consistently negative) IC across all windows
      - Shows low variance across bootstrap samples
      - Ranks in the top quartile in at least half the bootstrap runs

    Returns DataFrame with columns: feature, robust_score, mean_ic, ic_stability,
    window_consistency, n_top_quartile_hits.
    """
    if ic_windows is None:
        ic_windows = [250, 500, 1000]

    rng = np.random.default_rng(seed)
    cols = list(features.columns)
    n_feats = len(cols)
    n_bars = len(features)

    # Bootstrap stability per window
    window_rank_hits: dict[str, int] = {c: 0 for c in cols}
    window_mean_ic: dict[str, list[float]] = {c: [] for c in cols}
    ic_signs: dict[str, list[int]] = {c: [] for c in cols}

    for win in ic_windows:
        for b in range(n_bootstrap):
            # Sample a contiguous chunk (preserves time series structure
            # better than random bar shuffling)
            chunk = int(n_bars * bootstrap_frac)
            if chunk < win + 50:
                continue
            start = rng.integers(0, n_bars - chunk)
            sub_feats = features.iloc[start:start + chunk]
            sub_fwd = forward_returns.iloc[start:start + chunk]

            panel = compute_rolling_ic(sub_feats, sub_fwd, window=win)
            ic_mean = panel.mean().abs()  # magnitude
            ic_signed = panel.mean()       # with sign

            # Record IC sign
            for c in cols:
                v = float(ic_signed.get(c, 0.0))
                if np.isnan(v):
                    v = 0.0
                window_mean_ic[c].append(v)
                ic_signs[c].append(int(np.sign(v)))

            # Top-quartile hit count
            threshold = float(ic_mean.quantile(0.75))
            for c in cols:
                if float(ic_mean.get(c, 0.0)) >= threshold:
                    window_rank_hits[c] += 1

    total_runs = len(ic_windows) * n_bootstrap
    rows = []
    for c in cols:
        ics = window_mean_ic[c]
        if not ics:
            continue
        # Guard against NaN/inf from degenerate features (zero-variance windows)
        ics_arr = np.array(ics, dtype=np.float64)
        ics_arr = ics_arr[np.isfinite(ics_arr)]
        if len(ics_arr) == 0:
            continue
        mean_ic = float(np.mean(ics_arr))
        ic_std = float(np.std(ics_arr))
        if not np.isfinite(mean_ic) or not np.isfinite(ic_std):
            continue
        ic_stability = mean_ic / ic_std if ic_std > 1e-9 else 0.0
        # Sign consistency: how often the feature agrees with its mean direction
        target_sign = int(np.sign(mean_ic))
        sign_agreement = float(np.mean([s == target_sign for s in ic_signs[c]])) if target_sign != 0 else 0.0
        hits = window_rank_hits[c]
        hit_rate = hits / total_runs if total_runs > 0 else 0.0
        # Robust score: combines magnitude, stability, and consistency
        robust_score = abs(mean_ic) * sign_agreement * min(hit_rate * 2, 1.0)
        if not np.isfinite(robust_score):
            continue
        rows.append({
            "feature": c,
            "robust_score": round(robust_score, 5),
            "mean_ic": round(mean_ic, 5),
            "ic_stability": round(ic_stability, 3),
            "sign_consistency": round(sign_agreement, 3),
            "top_quartile_rate": round(hit_rate, 3),
        })
    df = pd.DataFrame(rows).sort_values("robust_score", ascending=False).reset_index(drop=True)
    return df


def select_features_robust(
    features: pd.DataFrame,
    forward_returns: pd.Series,
    *,
    top_n: int = 30,
    ic_windows: list[int] | None = None,
    n_bootstrap: int = 10,
    min_sign_consistency: float = 0.65,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One-shot robust feature selection using bootstrap + multi-window.

    Returns (filtered_features_df, ranking_df). Only features with
    sign_consistency >= min_sign_consistency are kept.
    """
    ranking = robust_feature_ranking(
        features,
        forward_returns,
        ic_windows=ic_windows,
        n_bootstrap=n_bootstrap,
    )
    kept = ranking[ranking["sign_consistency"] >= min_sign_consistency].head(top_n)
    selected = [c for c in kept["feature"].tolist() if c in features.columns]
    if not selected:
        logger.warning("no_robust_features_passed_filter")
        return features, ranking
    return features[selected], ranking


def select_features_walkforward(
    features: pd.DataFrame,
    forward_returns: pd.Series,
    *,
    train_ratio: float = 0.7,
    ic_window: int = 500,
    top_n: int = 50,
    min_ic_ir: float = 0.3,
) -> tuple[pd.DataFrame, FeatureImportanceReport]:
    """Walk-forward feature selection: rank on train, apply to test.

    Previously `select_features` computed IC on the full panel and then
    callers would "validate" on a subset they'd already seen during
    ranking — classic in-sample leakage that inflated reported IC.

    This function splits first: ranks features using only bars
    [0, split] and returns the report + filtered features. Downstream
    code should only validate on bars > split.
    """
    n = len(features)
    split = int(n * train_ratio)
    train_feats = features.iloc[:split]
    train_fwd = forward_returns.iloc[:split]

    ic_panel = compute_rolling_ic(train_feats, train_fwd, window=ic_window)
    report = rank_features(ic_panel, top_n=top_n, min_ic_ir=min_ic_ir)

    selected_cols = [c for c in report.top_features if c in features.columns]
    if not selected_cols:
        logger.warning("no_features_passed_ic_filter_walkforward")
        return features, report

    return features[selected_cols], report


def select_features(
    features: pd.DataFrame,
    forward_returns: pd.Series,
    *,
    ic_window: int = 500,
    top_n: int = 50,
    min_ic_ir: float = 0.3,
) -> tuple[pd.DataFrame, FeatureImportanceReport]:
    """One-shot feature selection: compute IC, rank, filter.

    NOTE: This function uses the full sample for ranking. For honest
    OOS validation, use `select_features_walkforward` instead.

    Returns:
        (filtered_features_df, importance_report)
    """
    ic_panel = compute_rolling_ic(features, forward_returns, window=ic_window)
    report = rank_features(ic_panel, top_n=top_n, min_ic_ir=min_ic_ir)

    selected_cols = [c for c in report.top_features if c in features.columns]
    if not selected_cols:
        # Fallback: return all features
        logger.warning("no_features_passed_ic_filter, using_all")
        return features, report

    logger.info(
        "feature_selection_complete",
        extra={
            "total": report.n_features_total,
            "stable": report.n_features_stable,
            "selected": len(selected_cols),
            "top3_ir": {c: report.ic_ir[c] for c in selected_cols[:3]},
        },
    )
    return features[selected_cols], report
