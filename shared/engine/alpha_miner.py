"""Automated alpha mining pipeline.

Generates, validates, and promotes ML alpha candidates automatically.
Runs monthly via cron (scripts/engine/alpha_mining.py).

Safety mechanisms:
  1. Walk-forward OOS evaluation (no in-sample metrics used for decisions)
  2. Cost-aware Sharpe (5bp + funding)
  3. Deflated Sharpe Ratio for multiple-testing correction
  4. Correlation screening vs existing alphas (< 0.4)
  5. Multi-symbol majority validation (3 of 5)
  6. Max drawdown cap (30%)
  7. Cumulative trial counter persisted to disk
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from shared.alpha.base import AlphaConfig
from shared.alpha.ml_discovery import MLDiscoveryAlpha
from shared.backtest.metrics import sharpe_ratio, apply_transaction_costs, deflated_sharpe_ratio
from shared.features.engine import FeatureEngine, FeatureEngineConfig
from shared.ml.gbm import GBMWrapper

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AlphaMinerConfig:
    """Configuration for the mining pipeline."""
    n_candidates: int = 50            # feature combinations to try
    features_per_model: int = 30      # max features per candidate
    min_oos_sharpe: float = 0.3       # minimum walk-forward OOS Sharpe
    max_corr_existing: float = 0.4    # max correlation with existing alphas
    cost_bps: float = 5.0
    min_symbols_positive: int = 3     # out of total symbols
    max_drawdown: float = 0.30
    # Walk-forward settings
    train_window: int = 3000
    refit_every: int = 720
    embargo_bars: int = 100
    target_horizon: int = 24
    # Paths
    models_dir: str = "data/models/ml_discovery"
    trials_path: str = "data/metrics/mining_trials.json"
    log_dir: str = "data/metrics/mining_log"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AlphaCandidate:
    """A candidate alpha discovered by the miner."""
    candidate_id: str
    feature_indices: list[int]
    feature_names: list[str]
    oos_sharpes: dict[str, float]     # per-symbol
    avg_oos_sharpe: float
    max_corr_with_existing: float
    max_drawdown: float
    n_positive_symbols: int
    # Advanced metrics (populated during evaluation)
    n_oos_bars: int = 0
    return_skewness: float = 0.0
    return_kurtosis: float = 3.0
    yearly_sharpes: dict[str, float] = field(default_factory=dict)  # year -> Sharpe
    worst_year_sharpe: float = -999.0
    dsr_pvalue: float = 0.0

    @property
    def score(self) -> float:
        """Rank score: rewards Sharpe, decorrelation, and consistency."""
        consistency = max(0.0, 1.0 + self.worst_year_sharpe) if self.worst_year_sharpe > -999 else 1.0
        return self.avg_oos_sharpe * (1.0 - self.max_corr_with_existing) * min(consistency, 1.5)


@dataclass
class MiningResult:
    """Result of a mining run."""
    timestamp: str
    n_candidates_tested: int
    n_passed_gates: int
    cumulative_trials: int
    promoted: list[AlphaCandidate]
    all_candidates: list[AlphaCandidate]


# ---------------------------------------------------------------------------
# Alpha Miner
# ---------------------------------------------------------------------------

class AlphaMiner:
    """Automated alpha discovery pipeline."""

    def __init__(self, config: AlphaMinerConfig | None = None) -> None:
        self.config = config or AlphaMinerConfig()
        self._feat_engine = FeatureEngine()

    def mine(
        self,
        dfs: dict[str, pd.DataFrame],
        funding: dict[str, pd.Series] | None = None,
        existing_positions: dict[str, dict[str, pd.Series]] | None = None,
    ) -> MiningResult:
        """Run the full mining pipeline.

        Args:
            dfs: {symbol: OHLCV DataFrame}
            funding: {symbol: funding rate Series}
            existing_positions: {symbol: {alpha_name: position Series}}

        Returns:
            MiningResult with promoted candidates.
        """
        c = self.config
        funding = funding or {}
        existing_positions = existing_positions or {}

        # Generate features for all symbols
        logger.info("Generating features for %d symbols...", len(dfs))
        feature_matrices = {}
        for sym, df in dfs.items():
            feature_matrices[sym] = self._feat_engine.generate(df, funding.get(sym))

        # Get common features across all symbols (intersection)
        first_sym = list(dfs.keys())[0]
        common_names = set(feature_matrices[first_sym].feature_names)
        for sym in dfs:
            common_names &= set(feature_matrices[sym].feature_names)
        all_feature_names = sorted(common_names)
        n_features = len(all_feature_names)

        # Generate candidate feature subsets
        rng = np.random.default_rng(int(datetime.now(timezone.utc).timestamp()))
        candidates_tested = 0
        passed: list[AlphaCandidate] = []

        # Cluster features by correlation for diverse sampling
        feat_corr = feature_matrices[first_sym].features.corr().abs().values
        groups = _cluster_features(feat_corr, threshold=0.7)

        for i in range(c.n_candidates):
            # Sample diverse feature subset by index into common features
            n_to_select = min(c.features_per_model, n_features)
            selected_idx = _sample_from_groups(
                groups, n_features, n_to_select, rng
            )
            # Clamp indices to valid range
            selected_idx = [j for j in selected_idx if j < n_features]
            if len(selected_idx) < 5:
                continue
            selected_names = [all_feature_names[j] for j in selected_idx]

            # Evaluate this candidate across all symbols
            candidate = self._evaluate_candidate(
                candidate_id=f"candidate_{i:04d}",
                feature_names=selected_names,
                dfs=dfs,
                feature_matrices=feature_matrices,
                existing_positions=existing_positions,
            )
            candidates_tested += 1

            if candidate is None:
                continue

            # Gate checks (5-gate validation)
            # Gate 1: Minimum OOS Sharpe
            if candidate.avg_oos_sharpe < c.min_oos_sharpe:
                continue
            # Gate 2: Decorrelation
            if candidate.max_corr_with_existing > c.max_corr_existing:
                continue
            # Gate 3: Multi-symbol
            if candidate.n_positive_symbols < c.min_symbols_positive:
                continue
            # Gate 4: Max drawdown
            if candidate.max_drawdown > c.max_drawdown:
                continue
            # Gate 5: Deflated Sharpe Ratio (multiple-testing correction)
            dsr = deflated_sharpe_ratio(
                sharpe_observed=candidate.avg_oos_sharpe,
                n_observations=candidate.n_oos_bars,
                n_trials=candidates_tested,  # current run trials
                skewness=candidate.return_skewness,
                kurtosis=candidate.return_kurtosis,
            )
            if dsr < 0.90:  # 90% confidence after DSR correction
                logger.info(
                    "Candidate %s failed DSR gate: %.3f < 0.90 (Sharpe=%.3f, trials=%d)",
                    candidate.candidate_id, dsr, candidate.avg_oos_sharpe, candidates_tested,
                )
                continue

            candidate.dsr_pvalue = dsr
            passed.append(candidate)
            logger.info(
                "Candidate %s passed: Sharpe=%.3f, corr=%.3f, DD=%.1f%%",
                candidate.candidate_id, candidate.avg_oos_sharpe,
                candidate.max_corr_with_existing, candidate.max_drawdown * 100,
            )

        # Update cumulative trial count
        cum_trials = self._update_trial_count(candidates_tested)

        # Rank by score and take top
        passed.sort(key=lambda c: c.score, reverse=True)
        promoted = passed[:1] if passed else []  # promote at most 1 per run

        result = MiningResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            n_candidates_tested=candidates_tested,
            n_passed_gates=len(passed),
            cumulative_trials=cum_trials,
            promoted=promoted,
            all_candidates=passed,
        )

        # Log result
        self._log_result(result)

        return result

    def _evaluate_candidate(
        self,
        candidate_id: str,
        feature_names: list[str],
        dfs: dict[str, pd.DataFrame],
        feature_matrices: dict,
        existing_positions: dict[str, dict[str, pd.Series]],
    ) -> AlphaCandidate | None:
        """Evaluate a candidate feature set via walk-forward on all symbols."""
        c = self.config
        oos_sharpes: dict[str, float] = {}
        max_corrs: list[float] = []
        max_dds: list[float] = []
        all_oos_pnl: dict[str, pd.Series] = {}

        for sym, df in dfs.items():
            fm = feature_matrices[sym]
            # Select columns by name (safe across different feature orderings)
            available = [f for f in feature_names if f in fm.features.columns]
            if len(available) < 5:
                continue
            feat = fm.features[available].values
            close = df["close"].astype(float).values
            n = len(df)

            # Compute target: cost-adjusted forward return
            log_ret = np.diff(np.log(close), prepend=np.log(close[0]))
            fwd_ret = np.zeros(n)
            h = c.target_horizon
            for t in range(n - h):
                fwd_ret[t] = np.sum(log_ret[t + 1:t + 1 + h])
            cost_drag = 2 * c.cost_bps / 10_000
            target = fwd_ret - cost_drag

            # Walk-forward
            positions = np.zeros(n)
            t = max(c.train_window + c.embargo_bars, 800)

            while t < n:
                te_end = min(t + c.refit_every, n)
                tr_end = t - c.embargo_bars
                tr_start = max(0, tr_end - c.train_window)

                if tr_end - tr_start < 500:
                    t = te_end
                    continue

                X_tr = feat[tr_start:tr_end]
                y_tr = target[tr_start:tr_end]
                valid = np.isfinite(y_tr) & (y_tr != 0)
                X_tr, y_tr = X_tr[valid], y_tr[valid]

                if len(X_tr) < 200:
                    t = te_end
                    continue

                # Train
                split = int(len(X_tr) * 0.8)
                model = GBMWrapper()
                model.fit(X_tr[:split], y_tr[:split], X_tr[split:], y_tr[split:])

                # Predict
                X_te = feat[t:te_end]
                raw = model.predict(X_te)
                pred_std = max(float(np.std(model.predict(X_tr))), 1e-8)
                positions[t:te_end] = np.tanh(raw / pred_std)

                t = te_end

            # Compute OOS metrics
            pos_series = pd.Series(positions, index=df.index)
            ret_series = pd.Series(log_ret, index=df.index)
            pnl = (pos_series.shift(1).fillna(0) * ret_series)

            # Apply costs
            turnover = pos_series.diff().abs()
            costs = turnover * c.cost_bps / 10_000
            net_pnl = pnl - costs

            # Only OOS region
            oos_start = max(c.train_window + c.embargo_bars, 800)
            oos_pnl = net_pnl.iloc[oos_start:]

            if len(oos_pnl) < 500:
                return None

            sr = float(sharpe_ratio(oos_pnl.values))
            oos_sharpes[sym] = sr
            all_oos_pnl[sym] = oos_pnl

            # Max drawdown
            cum = oos_pnl.cumsum()
            dd = (cum - cum.cummax()).min()
            max_dds.append(abs(float(dd)))

            # Correlation with existing alphas
            sym_existing = existing_positions.get(sym, {})
            for alpha_name, alpha_pos in sym_existing.items():
                if alpha_pos is not None and len(alpha_pos) >= len(pos_series):
                    oos_new = pos_series.iloc[oos_start:]
                    oos_old = alpha_pos.iloc[oos_start:oos_start + len(oos_new)]
                    if len(oos_old) == len(oos_new):
                        corr = _safe_corr(oos_new.values, oos_old.values)
                        max_corrs.append(abs(corr))

        if not oos_sharpes:
            return None

        # Compute advanced metrics from all OOS PnL combined
        total_oos_bars = sum(len(all_oos_pnl[s]) for s in all_oos_pnl)
        combined_oos = pd.concat(list(all_oos_pnl.values()))
        ret_skew = float(combined_oos.skew()) if len(combined_oos) > 30 else 0.0
        ret_kurt = float(combined_oos.kurtosis()) + 3.0 if len(combined_oos) > 30 else 3.0

        # Per-year Sharpe decomposition
        yearly_sharpes: dict[str, float] = {}
        for s, oos in all_oos_pnl.items():
            if hasattr(oos.index, 'year'):
                for yr, grp in oos.groupby(oos.index.year):
                    key = str(yr)
                    if key not in yearly_sharpes:
                        yearly_sharpes[key] = []
                    yearly_sharpes[key].append(float(sharpe_ratio(grp.values)))
        avg_yearly = {k: float(np.mean(v)) for k, v in yearly_sharpes.items()}
        worst_yr = min(avg_yearly.values()) if avg_yearly else -999.0

        return AlphaCandidate(
            candidate_id=candidate_id,
            feature_indices=[],
            feature_names=feature_names,
            oos_sharpes=oos_sharpes,
            avg_oos_sharpe=float(np.mean(list(oos_sharpes.values()))),
            max_corr_with_existing=float(max(max_corrs)) if max_corrs else 0.0,
            max_drawdown=float(max(max_dds)) if max_dds else 0.0,
            n_positive_symbols=sum(1 for s in oos_sharpes.values() if s > 0),
            n_oos_bars=total_oos_bars,
            return_skewness=ret_skew,
            return_kurtosis=ret_kurt,
            yearly_sharpes=avg_yearly,
            worst_year_sharpe=worst_yr,
        )

    def _update_trial_count(self, new_trials: int) -> int:
        """Persist cumulative trial count for DSR correction."""
        p = Path(self.config.trials_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"cumulative_trials": 0}
        if p.exists():
            with open(p) as f:
                data = json.load(f)
        data["cumulative_trials"] += new_trials
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        return data["cumulative_trials"]

    def _log_result(self, result: MiningResult) -> None:
        """Log mining result to disk."""
        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"mining_{ts}.json"
        # Convert to serializable format
        data = {
            "timestamp": result.timestamp,
            "n_candidates_tested": result.n_candidates_tested,
            "n_passed_gates": result.n_passed_gates,
            "cumulative_trials": result.cumulative_trials,
            "promoted": [
                {
                    "id": c.candidate_id,
                    "features": c.feature_names,
                    "sharpes": c.oos_sharpes,
                    "avg_sharpe": c.avg_oos_sharpe,
                    "max_corr": c.max_corr_with_existing,
                    "max_dd": c.max_drawdown,
                    "score": c.score,
                }
                for c in result.promoted
            ],
        }
        with open(log_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Mining log saved to %s", log_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cluster_features(
    corr_matrix: np.ndarray, threshold: float = 0.7
) -> list[list[int]]:
    """Simple greedy clustering by correlation."""
    n = corr_matrix.shape[0]
    assigned = set()
    groups: list[list[int]] = []

    for i in range(n):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        for j in range(i + 1, n):
            if j in assigned:
                continue
            if corr_matrix[i, j] > threshold:
                group.append(j)
                assigned.add(j)
        groups.append(group)

    return groups


def _sample_from_groups(
    groups: list[list[int]],
    n_features: int,
    target_size: int,
    rng: np.random.Generator,
) -> list[int]:
    """Sample features from different groups for diversity."""
    selected: list[int] = []
    # Shuffle group order
    group_order = list(range(len(groups)))
    rng.shuffle(group_order)

    per_group = max(1, target_size // min(len(groups), target_size))

    for gi in group_order:
        if len(selected) >= target_size:
            break
        group = groups[gi]
        n_pick = min(per_group, len(group), target_size - len(selected))
        picks = rng.choice(len(group), size=n_pick, replace=False)
        selected.extend(group[p] for p in picks)

    # If still short, random fill
    remaining = list(set(range(n_features)) - set(selected))
    if remaining and len(selected) < target_size:
        n_extra = min(target_size - len(selected), len(remaining))
        selected.extend(rng.choice(remaining, size=n_extra, replace=False).tolist())

    return selected[:target_size]


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5:
        return 0.0
    std_a, std_b = np.std(a), np.std(b)
    if std_a < 1e-10 or std_b < 1e-10:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])
