"""Deflated Sharpe Ratio (DSR) + Probability of Backtest Overfitting (PBO).

Both from Bailey & López de Prado. Used to answer the question institutional
allocators always ask — *is the reported Sharpe genuine or a multiple-testing
artifact?*

- **Probabilistic Sharpe Ratio (PSR)**: P(true SR > benchmark | observed SR, n,
  skew, kurt). Corrects for finite sample + higher moments.
- **Deflated Sharpe Ratio (DSR)**: PSR with the benchmark set to the *expected
  max* SR under the null given `n_trials` tried. Required honesty metric when
  any grid search / family of strategies was evaluated.
- **PBO via CSCV**: Combinatorially-Symmetric Cross-Validation. Partitions
  returns into S chunks, forms all C(S, S/2) train/test combinations, and
  measures how often the in-sample top strategy underperforms out-of-sample.
  PBO > 0.5 ⇒ the selection procedure is noise-driven.

Pure numpy/scipy. No external deps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy import stats as sp_stats


EULER_MASCHERONI = 0.5772156649


def sharpe_ratio(returns: np.ndarray, periods_per_year: float = 24 * 365) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods_per_year))


def probabilistic_sharpe_ratio(
    returns: np.ndarray,
    sr_benchmark: float = 0.0,
    periods_per_year: float = 24 * 365,
) -> float:
    """P(SR > sr_benchmark) given observed SR, skew and kurt.

    Both SRs are *annualized*. Returns a probability in [0, 1].
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return float("nan")
    sr_hat = sharpe_ratio(r, periods_per_year)
    # SR on per-bar basis
    sr_bar = sr_hat / math.sqrt(periods_per_year)
    sr_bench_bar = sr_benchmark / math.sqrt(periods_per_year)
    skew = float(sp_stats.skew(r, bias=False))
    kurt = float(sp_stats.kurtosis(r, bias=False, fisher=True))  # excess kurt
    denom = math.sqrt(max(
        (1 - skew * sr_bar + (kurt / 4.0) * sr_bar ** 2) / max(n - 1, 1),
        1e-12,
    ))
    z = (sr_bar - sr_bench_bar) / denom
    return float(sp_stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_std_across_trials: float = 1.0) -> float:
    """E[max SR] under the null hypothesis that all trials have SR=0.

    Uses the standard extreme-value approximation for the max of n_trials iid
    standard normals: z* ≈ (1 - γ) Φ⁻¹(1 - 1/n) + γ Φ⁻¹(1 - 1/(n·e)).
    Multiply by *sr_std_across_trials* — the cross-trial dispersion in SR —
    to get the expected max in SR space.
    """
    if n_trials <= 1:
        return 0.0
    g = EULER_MASCHERONI
    z1 = sp_stats.norm.ppf(1 - 1.0 / n_trials)
    z2 = sp_stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
    z_star = (1 - g) * z1 + g * z2
    return float(z_star * sr_std_across_trials)


def deflated_sharpe_ratio(
    returns: np.ndarray,
    n_trials: int,
    sr_std_across_trials: float,
    periods_per_year: float = 24 * 365,
) -> dict:
    """DSR = PSR with benchmark = E[max SR | n_trials]. Per-bar SR space.

    *sr_std_across_trials* is the standard deviation of annualized SRs across
    the strategies you tried (required — otherwise the correction collapses).
    """
    sr_bench_annual = expected_max_sharpe(n_trials, sr_std_across_trials)
    psr = probabilistic_sharpe_ratio(returns, sr_bench_annual, periods_per_year)
    sr_hat = sharpe_ratio(returns, periods_per_year)
    return {
        "sr_hat": round(sr_hat, 4),
        "sr_benchmark": round(sr_bench_annual, 4),
        "dsr": round(psr, 4) if not math.isnan(psr) else None,
        "n_trials": int(n_trials),
        "sr_std_across_trials": round(sr_std_across_trials, 4),
        "verdict": (
            "genuine" if psr and psr >= 0.95 else
            "marginal" if psr and psr >= 0.7 else
            "suspect"
        ),
    }


# ---------------------------------------------------------------------------
# Combinatorially-Symmetric Cross-Validation (CSCV) → PBO
# ---------------------------------------------------------------------------


@dataclass
class PBOResult:
    pbo: float                # Probability of Backtest Overfit
    n_combinations: int
    median_is_sharpe: float   # among selected winners, in-sample Sharpe
    median_oos_sharpe: float  # their corresponding OOS Sharpe
    sharpe_degradation: float # median IS − median OOS
    stochastic_dominance: float  # P(winner's OOS rank ≤ median | IS rank = top)


def pbo_cscv(
    strategy_returns: np.ndarray,
    s_chunks: int = 16,
    periods_per_year: float = 24 * 365,
) -> PBOResult:
    """Bailey-López de Prado PBO via CSCV.

    Args:
        strategy_returns: (T, N) array — rows are time, columns are strategies.
        s_chunks: must be even. Higher = more combinations but smaller chunks.

    The metric: across every possible train/test split (non-overlapping halves
    of the S chunks), pick the strategy that won in-sample, measure its OOS
    rank. PBO = fraction of splits where winner's OOS rank is below median.
    """
    X = np.asarray(strategy_returns, dtype=float)
    if X.ndim != 2:
        raise ValueError("strategy_returns must be 2D (T×N)")
    T, N = X.shape
    if N < 2 or T < s_chunks * 2:
        return PBOResult(float("nan"), 0, 0.0, 0.0, 0.0, 0.0)
    s_chunks = s_chunks - (s_chunks % 2)  # force even
    # Split rows into S roughly-equal chunks.
    edges = np.linspace(0, T, s_chunks + 1, dtype=int)
    chunks = [X[edges[i]:edges[i + 1]] for i in range(s_chunks)]
    half = s_chunks // 2
    logits = []
    is_sharpes = []
    oos_sharpes = []
    for train_idx in combinations(range(s_chunks), half):
        train_set = set(train_idx)
        test_idx = [i for i in range(s_chunks) if i not in train_set]
        r_train = np.vstack([chunks[i] for i in train_idx])
        r_test = np.vstack([chunks[i] for i in test_idx])
        sr_train = _per_col_sharpe(r_train, periods_per_year)
        sr_test = _per_col_sharpe(r_test, periods_per_year)
        winner = int(np.argmax(sr_train))
        # Rank of winner in test set (higher = better)
        rank_test = sp_stats.rankdata(sr_test)[winner]
        relative_rank = rank_test / (N + 1)  # in (0, 1)
        # Logit of relative rank
        rr = min(max(relative_rank, 1e-6), 1 - 1e-6)
        logit = math.log(rr / (1 - rr))
        logits.append(logit)
        is_sharpes.append(sr_train[winner])
        oos_sharpes.append(sr_test[winner])
    logits_arr = np.asarray(logits)
    pbo = float((logits_arr < 0).mean())
    return PBOResult(
        pbo=round(pbo, 4),
        n_combinations=len(logits),
        median_is_sharpe=round(float(np.median(is_sharpes)), 4),
        median_oos_sharpe=round(float(np.median(oos_sharpes)), 4),
        sharpe_degradation=round(float(np.median(is_sharpes) - np.median(oos_sharpes)), 4),
        stochastic_dominance=round(float((logits_arr >= 0).mean()), 4),
    )


def _per_col_sharpe(X: np.ndarray, periods_per_year: float) -> np.ndarray:
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=1)
    sd = np.where(sd == 0, np.nan, sd)
    sr = mu / sd * math.sqrt(periods_per_year)
    return np.nan_to_num(sr, nan=0.0)
