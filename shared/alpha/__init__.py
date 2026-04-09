"""Alpha library — distinct, named, individually-backtestable strategies.

Each Alpha takes an OHLCV DataFrame and produces a position series in [-1, 1]
indexed identically. Strategies are deterministic and stateless beyond their
configured parameters so they can be backtested, ensembled, and shadow-traded
independently.

This sits on top of the existing `shared/factors/` library (which scores
point-in-time feature dicts) and the existing `shared/formulas/` library
(which produces composite signal numbers). The alpha layer is what the
strategy-registry actually persists and what the portfolio ensemble combines.
"""

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal
from shared.alpha.registry import ALPHA_REGISTRY, get_alpha, list_alphas
from shared.alpha.trend_breakout import TrendBreakoutAlpha
from shared.alpha.mean_reversion import MeanReversionAlpha
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha
from shared.alpha.vol_breakout import VolBreakoutAlpha
from shared.alpha.carry import CarryAlpha
from shared.alpha.stat_arb import StatArbAlpha
from shared.alpha.cross_sectional import CrossSectionalMomentumAlpha

__all__ = [
    "Alpha",
    "AlphaConfig",
    "AlphaSignal",
    "ALPHA_REGISTRY",
    "get_alpha",
    "list_alphas",
    "TrendBreakoutAlpha",
    "MeanReversionAlpha",
    "MomentumEnsembleAlpha",
    "VolBreakoutAlpha",
    "CarryAlpha",
    "StatArbAlpha",
    "CrossSectionalMomentumAlpha",
]
