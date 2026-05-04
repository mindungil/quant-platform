"""Portfolio construction utilities.

Submodules are imported lazily via PEP 562 __getattr__ so that a
service that only needs one (e.g. signal-service → meta_ensemble.combine)
doesn't pay the import cost of hrp (scipy) or nco (statsmodels).

Usage:
    from shared.portfolio import EnsembleAllocator      # via __getattr__
    from shared.portfolio.meta_ensemble import combine  # direct, always works
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "hrp_weights",
    "EnsembleAllocator",
    "EnsembleConfig",
    "EnsembleResult",
    "AttributionReport",
    "attribute",
    "build_regime_proba",
    "load_regime_affinity",
    "regime_fit_summary",
    "NCOConfig",
    "nco_weights",
    "denoise_corr",
    "marchenko_pastur_max",
]

_LAZY = {
    "hrp_weights": ("shared.portfolio.hrp", "hrp_weights"),
    "EnsembleAllocator": ("shared.portfolio.ensemble", "EnsembleAllocator"),
    "EnsembleConfig": ("shared.portfolio.ensemble", "EnsembleConfig"),
    "EnsembleResult": ("shared.portfolio.ensemble", "EnsembleResult"),
    "AttributionReport": ("shared.portfolio.attribution", "AttributionReport"),
    "attribute": ("shared.portfolio.attribution", "attribute"),
    "NCOConfig": ("shared.portfolio.nco", "NCOConfig"),
    "nco_weights": ("shared.portfolio.nco", "nco_weights"),
    "denoise_corr": ("shared.portfolio.nco", "denoise_corr"),
    "marchenko_pastur_max": ("shared.portfolio.nco", "marchenko_pastur_max"),
    "KellyStore": ("shared.portfolio.kelly_store", "KellyStore"),
    "default_kelly_store": ("shared.portfolio.kelly_store", "default_store"),
    # Phase 2 modules
    "MetaEnsembleConfig": ("shared.portfolio.meta_ensemble", "MetaEnsembleConfig"),
    "combine": ("shared.portfolio.meta_ensemble", "combine"),
    "cvar_overlay": ("shared.portfolio.meta_ensemble", "cvar_overlay"),
    "cvar_min": ("shared.portfolio.cvar", "cvar_min"),
    "CVaRConfig": ("shared.portfolio.cvar", "CVaRConfig"),
    "historical_cvar": ("shared.portfolio.cvar", "historical_cvar"),
    "fit_pca_factor_model": ("shared.portfolio.risk_model", "fit_pca_factor_model"),
    "FactorModel": ("shared.portfolio.risk_model", "FactorModel"),
    "brinson_attribution": ("shared.portfolio.risk_model", "brinson_attribution"),
    "black_litterman": ("shared.portfolio.black_litterman", "black_litterman"),
    "reverse_optimize": ("shared.portfolio.black_litterman", "reverse_optimize"),
    # Regime-conditional ensemble helpers
    "build_regime_proba": ("shared.portfolio.regime_ensemble", "build_regime_proba"),
    "load_regime_affinity": ("shared.portfolio.regime_ensemble", "load_regime_affinity"),
    "regime_fit_summary": ("shared.portfolio.regime_ensemble", "regime_fit_summary"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'shared.portfolio' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value  # cache for subsequent access
    return value
