"""Factor registry -- single point of access for all factors."""
from __future__ import annotations

from shared.factors.base import Factor

ALL_FACTORS: list[Factor] = []

# Built-in factor packs. Public (open) packs first — these stay in
# quant-platform. Private packs (IC engine, kimchi premium, derivatives,
# sentiment, research alphas, worldquant alphas) are loaded best-effort
# and replaced/augmented by plugin modules in private builds.
try:
    from shared.factors.technical import TECHNICAL_FACTORS
    from shared.factors.momentum import MOMENTUM_FACTORS
    from shared.factors.mean_reversion import MEAN_REVERSION_FACTORS
    from shared.factors.volatility import VOLATILITY_FACTORS
    ALL_FACTORS.extend(TECHNICAL_FACTORS)
    ALL_FACTORS.extend(MOMENTUM_FACTORS)
    ALL_FACTORS.extend(MEAN_REVERSION_FACTORS)
    ALL_FACTORS.extend(VOLATILITY_FACTORS)
except ImportError:
    pass

try:
    from shared.factors.derivatives import DERIVATIVES_FACTORS
    from shared.factors.sentiment import SENTIMENT_FACTORS
    from shared.factors.kimchi_premium import KIMCHI_PREMIUM_FACTORS
    from shared.factors.research_alpha import RESEARCH_ALPHA_FACTORS
    from shared.factors.worldquant_alphas import WORLDQUANT_ALPHA_FACTORS
    ALL_FACTORS.extend(DERIVATIVES_FACTORS)
    ALL_FACTORS.extend(SENTIMENT_FACTORS)
    ALL_FACTORS.extend(KIMCHI_PREMIUM_FACTORS)
    ALL_FACTORS.extend(RESEARCH_ALPHA_FACTORS)
    ALL_FACTORS.extend(WORLDQUANT_ALPHA_FACTORS)
except ImportError:
    # Public-only build — IP factor packs absent. Plugins fill the gap.
    pass


def get_all() -> list[Factor]:
    """Return all registered factors."""
    return ALL_FACTORS


def get_by_category(category: str) -> list[Factor]:
    """Return factors filtered by category."""
    return [f for f in ALL_FACTORS if f.category == category]


def compute_all(features: dict) -> dict[str, float]:
    """Compute all factors and return a name -> score mapping."""
    return {f.name: f.compute(features) for f in ALL_FACTORS}


# ──────────────────────────────────────────────────────────────────
# Plugin loader (open-core seam)
# ──────────────────────────────────────────────────────────────────
# Private factor packs can extend ALL_FACTORS at import time via
# QUANT_FACTOR_PLUGINS. Each listed module should call register_factors(...)
# during its import.


def register_factors(factors: list[Factor]) -> None:
    """Append plugin-supplied factors to ALL_FACTORS."""
    ALL_FACTORS.extend(factors)


def load_plugins() -> None:
    import importlib
    import logging
    import os

    log = logging.getLogger(__name__)
    plugins = os.environ.get("QUANT_FACTOR_PLUGINS", "")
    for mod_name in plugins.split(","):
        mod_name = mod_name.strip()
        if not mod_name:
            continue
        try:
            importlib.import_module(mod_name)
            log.info("factor_plugin_loaded", extra={"module": mod_name})
        except Exception as exc:
            log.warning(
                "factor_plugin_load_failed",
                extra={"module": mod_name, "error": str(exc)[:200]},
            )


load_plugins()
