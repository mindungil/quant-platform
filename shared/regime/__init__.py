"""Market regime detection.

Two detectors:

  - VolTrendRegime — fast, deterministic 4-state classifier (trend up,
    trend down, range, crisis) based on rolling vol z-score and trend
    z-score. No training required.

  - HMMRegime — Gaussian-emission Hidden Markov Model with K states fit
    on log returns via Baum-Welch. Slower but adapts to the data.

Both produce a per-bar regime label and a state-probability matrix that
the EnsembleAllocator can use to dynamically reweight alphas.
"""

from shared.regime.detector import VolTrendRegime, HMMRegime, RegimeOutput

__all__ = ["VolTrendRegime", "HMMRegime", "RegimeOutput"]
