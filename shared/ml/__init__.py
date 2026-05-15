"""Lightweight ML primitives.

`cv.PurgedKFold` is public (standard López de Prado). Proprietary additions
(CPCV, RF, online learners, uniqueness weighting) are present only in
private builds — public-only builds get safe fallbacks via plugins.
"""
from shared.ml.cv import PurgedKFold, purged_kfold_split

__all__ = ["PurgedKFold", "purged_kfold_split"]

try:
    from shared.ml.cpcv import CombinatorialPurgedCV
    __all__.append("CombinatorialPurgedCV")
except ImportError:
    CombinatorialPurgedCV = None  # type: ignore

try:
    from shared.ml.trees import RandomForestRegressor, RegressionTree
    __all__.extend(["RandomForestRegressor", "RegressionTree"])
except ImportError:
    RandomForestRegressor = RegressionTree = None  # type: ignore

try:
    from shared.ml.online import OnlineRidge, RecursiveLeastSquares
    __all__.extend(["OnlineRidge", "RecursiveLeastSquares"])
except ImportError:
    OnlineRidge = RecursiveLeastSquares = None  # type: ignore

try:
    from shared.ml.uniqueness import average_uniqueness
    __all__.append("average_uniqueness")
except ImportError:
    average_uniqueness = None  # type: ignore
