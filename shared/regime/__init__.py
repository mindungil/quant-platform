"""Market regime detection.

Two detectors:

  - VolTrendRegime — fast, deterministic 4-state classifier (trend up,
    trend down, range, crisis) based on rolling vol z-score and trend
    z-score. No training required.

  - HMMRegime — Gaussian-emission Hidden Markov Model with K states fit
    on log returns via Baum-Welch. Slower but adapts to the data.

Both produce a per-bar regime label and a state-probability matrix that
the EnsembleAllocator can use to dynamically reweight alphas.

The legacy feature-dict-based `detect_regime`/`suggest_formula_type` API
is re-exported for backward compatibility with existing agent code.
"""

from shared.regime.composite import (
    AdxHurstRegime,
    CompositeOutput,
    CompositeRegime,
    TREND_STATES,
    VOL_STATES,
    VolQuantileRegime,
)
from shared.regime.detector import HMMRegime, RegimeOutput, VolTrendRegime
from shared.regime.enhanced import EnhancedRegime
from shared.regime.legacy import (
    MarketRegime,
    RegimeDetector,
    detect_regime,
    suggest_formula_type,
)


# Canonical hard-coded affinity priors for VolTrendRegime's 4 states. Used by
# generate_signals.py and the research backtest scripts as the static fallback
# when no fitted affinity (data/state/regime_alpha_fit.json) is available.
# Keys must match VolTrendRegime.STATE_NAMES exactly.
DEFAULT_AFFINITY: dict[str, dict[str, float]] = {
    "momentum_ensemble": {"TREND_UP": 1.4, "TREND_DOWN": 1.4, "RANGE": 0.5, "CRISIS": 0.4},
    "trend_breakout":    {"TREND_UP": 1.5, "TREND_DOWN": 1.5, "RANGE": 0.4, "CRISIS": 0.6},
    "vol_breakout":      {"TREND_UP": 1.2, "TREND_DOWN": 1.2, "RANGE": 0.5, "CRISIS": 1.0},
    "range_reversion":   {"TREND_UP": 0.5, "TREND_DOWN": 0.5, "RANGE": 1.3, "CRISIS": 0.7},
    "funding_carry":     {"TREND_UP": 0.8, "TREND_DOWN": 0.8, "RANGE": 1.0, "CRISIS": 1.2},
}

__all__ = [
    "VolTrendRegime",
    "HMMRegime",
    "EnhancedRegime",
    "RegimeOutput",
    "VolQuantileRegime",
    "AdxHurstRegime",
    "CompositeRegime",
    "CompositeOutput",
    "VOL_STATES",
    "TREND_STATES",
    "MarketRegime",
    "RegimeDetector",
    "detect_regime",
    "suggest_formula_type",
    "DEFAULT_AFFINITY",
]
