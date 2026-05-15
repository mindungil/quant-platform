"""Cross-Sectional ML Alpha.

Generates long-short signals across a panel of symbols using relative
features (relative momentum, relative volume, relative volatility, relative
funding). Uses a simple ridge regression model retrained walk-forward.

Why this is structurally uncorrelated with existing trend alphas:
- Trend alphas predict "BTC will go up" (absolute direction)
- Cross-sectional predicts "ETH will outperform BTC" (relative)
- Market-direction neutral by construction

Architecture:
  Panel of 5 symbols → cross-sectional features per bar
  Ridge regression → predicted relative return ranks
  Long top-ranked, short bottom-ranked (dollar-neutral)
  Walk-forward retrained every 720 bars (1 month)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal, ema, rolling_zscore


_DEFAULT_PARAMS = {
    "lookback_windows": [24, 72, 168],
    "rebalance_every": 24,
    "train_window": 3000,
    "refit_every": 720,
    "embargo": 48,
    "target_horizon": 24,
    "cost_bps": 5.0,
    "ridge_alpha": 1.0,
}


class CrossSectionalMLAlpha(Alpha):
    """Panel-based cross-sectional ML alpha."""

    def __init__(self, config: AlphaConfig | None = None) -> None:
        if config is None:
            config = AlphaConfig(name="cross_sectional_ml", params=dict(_DEFAULT_PARAMS))
        merged = dict(_DEFAULT_PARAMS)
        merged.update(config.params)
        config.params = merged
        super().__init__(config)

    def _generate(self, df_or_dict) -> pd.Series:
        """Requires dict input: {symbol: OHLCV DataFrame}."""
        if not isinstance(df_or_dict, dict):
            raise TypeError("CrossSectionalMLAlpha requires dict of {symbol: df}")
        return self._generate_panel_aggregate(df_or_dict)

    def generate_per_asset(
        self, dfs: dict[str, pd.DataFrame],
    ) -> dict[str, pd.Series]:
        """Return per-symbol position series."""
        p = self.config.params
        symbols = sorted(dfs.keys())
        if len(symbols) < 3:
            return {s: pd.Series(0.0, index=dfs[s].index) for s in symbols}

        # Align indices
        common_idx = dfs[symbols[0]].index
        for s in symbols[1:]:
            common_idx = common_idx.intersection(dfs[s].index)
        common_idx = common_idx.sort_values()

        if len(common_idx) < 1000:
            return {s: pd.Series(0.0, index=dfs[s].index) for s in symbols}

        # Build cross-sectional feature panel: (n_bars, n_symbols, n_features)
        features_per_sym, feature_names = _build_cross_sectional_features(
            dfs, symbols, common_idx, p["lookback_windows"]
        )
        n_bars = len(common_idx)
        n_syms = len(symbols)
        n_feat = len(feature_names)

        # Build target: forward return rank (cross-sectional)
        fwd_rets = np.zeros((n_bars, n_syms))
        h = p["target_horizon"]
        for si, s in enumerate(symbols):
            close = dfs[s].loc[common_idx, "close"].astype(float).values
            log_ret = np.diff(np.log(close), prepend=np.log(close[0]))
            for t in range(n_bars - h):
                fwd_rets[t, si] = np.sum(log_ret[t + 1:t + 1 + h])

        # Walk-forward ridge regression
        positions = {s: np.zeros(n_bars) for s in symbols}
        train_w = p["train_window"]
        refit = p["refit_every"]
        embargo = p["embargo"]
        ridge_alpha = p["ridge_alpha"]

        t = train_w + embargo
        while t < n_bars:
            te_end = min(t + refit, n_bars)
            tr_end = t - embargo
            tr_start = max(0, tr_end - train_w)

            if tr_end - tr_start < 500:
                t = te_end
                continue

            # Flatten train data: each (bar, symbol) is a sample
            X_train = []
            y_train = []
            for ti in range(tr_start, tr_end):
                for si in range(n_syms):
                    X_train.append(features_per_sym[ti, si, :])
                    y_train.append(fwd_rets[ti, si])

            X_train = np.array(X_train)
            y_train = np.array(y_train)

            # Remove NaN
            valid = np.isfinite(y_train) & np.all(np.isfinite(X_train), axis=1)
            X_train, y_train = X_train[valid], y_train[valid]

            if len(X_train) < 100:
                t = te_end
                continue

            # Ridge regression (closed-form, no sklearn needed)
            w = _ridge_fit(X_train, y_train, ridge_alpha)

            # Predict test period
            for ti in range(t, te_end):
                preds = np.zeros(n_syms)
                for si in range(n_syms):
                    feat_vec = features_per_sym[ti, si, :]
                    if np.all(np.isfinite(feat_vec)):
                        preds[si] = feat_vec @ w

                # Cross-sectional rank → position
                # Demean predictions to make dollar-neutral
                preds -= np.mean(preds)
                pred_std = np.std(preds)
                if pred_std > 1e-10:
                    preds /= pred_std

                # tanh to bound
                for si, s in enumerate(symbols):
                    positions[s][ti] = np.tanh(preds[si])

            t = te_end

        # Convert to Series
        result = {}
        for s in symbols:
            full_pos = pd.Series(0.0, index=dfs[s].index)
            aligned = pd.Series(positions[s], index=common_idx)
            full_pos.loc[common_idx] = aligned.values
            result[s] = full_pos

        return result

    def _generate_panel_aggregate(self, dfs: dict[str, pd.DataFrame]) -> pd.Series:
        """Aggregate per-asset signals into a single basket signal."""
        per_asset = self.generate_per_asset(dfs)
        if not per_asset:
            return pd.Series(dtype=float)
        # Average absolute position across symbols
        all_pos = pd.DataFrame(per_asset)
        return all_pos.mean(axis=1).fillna(0.0)


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------

def _build_cross_sectional_features(
    dfs: dict[str, pd.DataFrame],
    symbols: list[str],
    common_idx: pd.DatetimeIndex,
    lookback_windows: list[int],
) -> tuple[np.ndarray, list[str]]:
    """Build cross-sectional feature panel.

    Returns:
        features: array of shape (n_bars, n_symbols, n_features)
        feature_names: list of feature names
    """
    n_bars = len(common_idx)
    n_syms = len(symbols)

    # Per-symbol raw features
    raw_features: dict[str, dict[str, np.ndarray]] = {}
    for s in symbols:
        df = dfs[s].loc[common_idx]
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        log_ret = np.log(close / close.shift(1)).fillna(0)

        feats: dict[str, np.ndarray] = {}
        for w in lookback_windows:
            feats[f"ret_{w}"] = close.pct_change(w).fillna(0).values
            feats[f"vol_{w}"] = log_ret.rolling(w, min_periods=w // 2).std().fillna(0).values
            feats[f"volume_z_{w}"] = rolling_zscore(volume, w).values
        raw_features[s] = feats

    feature_names_raw = list(raw_features[symbols[0]].keys())

    # Cross-sectional features: rank of each feature across symbols
    cs_feature_names = []
    for fn in feature_names_raw:
        cs_feature_names.append(f"rank_{fn}")
        cs_feature_names.append(f"demean_{fn}")

    n_feat = len(cs_feature_names)
    features = np.zeros((n_bars, n_syms, n_feat))

    for t in range(n_bars):
        for fi, fn in enumerate(feature_names_raw):
            vals = np.array([raw_features[s][fn][t] for s in symbols])
            mean_v = np.mean(vals)
            std_v = np.std(vals)

            # Rank feature
            ranks = _rank_array(vals)

            # Demeaned feature
            demeaned = (vals - mean_v) / max(std_v, 1e-10)

            for si in range(n_syms):
                features[t, si, fi * 2] = ranks[si]
                features[t, si, fi * 2 + 1] = demeaned[si]

    # Replace NaN/Inf
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

    return features, cs_feature_names


def _rank_array(arr: np.ndarray) -> np.ndarray:
    """Rank values in [-1, 1] range (normalized)."""
    n = len(arr)
    order = arr.argsort()
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.linspace(-1, 1, n)
    return ranks


def _ridge_fit(X: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    """Closed-form ridge regression: w = (X'X + αI)^{-1} X'y."""
    n_feat = X.shape[1]
    XtX = X.T @ X + alpha * np.eye(n_feat)
    Xty = X.T @ y
    try:
        w = np.linalg.solve(XtX, Xty)
    except np.linalg.LinAlgError:
        w = np.zeros(n_feat)
    return w
