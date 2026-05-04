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
]
