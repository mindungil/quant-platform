"""Alpha registry — single source of truth for available alphas.

The strategy-registry persists strategies that name an alpha here plus an
override params dict. Backtest, shadow, and live execution all dispatch
through this registry.

Alphas are split across two tiers:

  PRODUCTION_READY — alphas the meta-ensemble is allowed to pick by
    default. Requirements: runs on pure OHLCV (no exogenous data
    plumbing needed at instantiation), doesn't exhibit catastrophic
    drawdown or numerical blow-up on 8-yr BTC hourly. Tagged based on
    `scripts/alpha_audit.py` results.

  EXPERIMENTAL — importable but NOT included in the default meta-
    ensemble alpha list. Either requires injection (funding, panel,
    fng) or has known issues needing fixes. Listed here so the
    incubator, strategy-registry, and research notebooks can still
    reach them explicitly.

The full ALPHA_REGISTRY keeps every alpha addressable by name so
existing strategies in the DB don't break; callers that want only
safe alphas should use `list_production_alphas()`.
"""
from __future__ import annotations

from typing import Callable

from shared.alpha.base import Alpha, AlphaConfig
from shared.alpha.carry import CarryAlpha
from shared.alpha.funding_carry import FundingCarryAlpha
from shared.alpha.funding_momentum import FundingMomentumAlpha
from shared.alpha.cross_sectional import CrossSectionalMomentumAlpha
from shared.alpha.kalman_trend import KalmanTrendAlpha
from shared.alpha.mean_reversion import MeanReversionAlpha
from shared.alpha.ml_forest import MetaForestAlpha
from shared.alpha.ml_meta import MetaMLAlpha
from shared.alpha.online_rls import OnlineRLSAlpha
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha
from shared.alpha.stat_arb import StatArbAlpha
from shared.alpha.trend_breakout import TrendBreakoutAlpha
from shared.alpha.cross_sectional_ml import CrossSectionalMLAlpha
from shared.alpha.derivatives_alpha import DerivativesAlpha
from shared.alpha.ml_discovery import MLDiscoveryAlpha
from shared.alpha.ml_meta_alpha import MLMetaAlpha
from shared.alpha.fear_greed import FearGreedAlpha
from shared.alpha.cross_asset import CrossAssetAlpha
from shared.alpha.range_reversion import RangeReversionAlpha
from shared.alpha.vol_breakout import VolBreakoutAlpha
from shared.alpha.rv_ratio_breakout import RvRatioBreakoutAlpha
from shared.alpha.oi_divergence import OiDivergenceAlpha
from shared.alpha.lsr_contrarian import LsrContrarianAlpha
from shared.alpha.technical_ensemble import TechnicalEnsembleAlpha
from shared.alpha.external_context import ExternalContextAlpha
from shared.alpha.macro_context import MacroContextAlpha
from shared.alpha.flow_sentiment import FlowSentimentAlpha
from shared.alpha.news_impact import NewsImpactAlpha


# Each entry: name -> factory(config) -> Alpha
ALPHA_REGISTRY: dict[str, Callable[..., Alpha]] = {
    "trend_breakout": lambda cfg=None: TrendBreakoutAlpha(cfg),
    "mean_reversion": lambda cfg=None: MeanReversionAlpha(cfg),
    "momentum_ensemble": lambda cfg=None: MomentumEnsembleAlpha(cfg),
    "vol_breakout": lambda cfg=None: VolBreakoutAlpha(cfg),
    "carry": lambda cfg=None: CarryAlpha(cfg),
    "funding_carry": lambda cfg=None, symbol="BTCUSDT": FundingCarryAlpha(cfg, symbol=symbol),
    "funding_momentum": lambda cfg=None, symbol="BTCUSDT": FundingMomentumAlpha(cfg, symbol=symbol),
    "stat_arb": lambda cfg=None: StatArbAlpha(cfg),
    "cross_sectional_momentum": lambda cfg=None: CrossSectionalMomentumAlpha(cfg),
    "ml_meta": lambda cfg=None: MetaMLAlpha(cfg),
    "kalman_trend": lambda cfg=None: KalmanTrendAlpha(cfg),
    "ml_forest": lambda cfg=None: MetaForestAlpha(cfg),
    "online_rls": lambda cfg=None: OnlineRLSAlpha(cfg),
    "ml_discovery": lambda cfg=None: MLDiscoveryAlpha(cfg),
    "ml_meta_alpha": lambda cfg=None: MLMetaAlpha(cfg),
    "derivatives_alpha": lambda cfg=None: DerivativesAlpha(cfg),
    "cross_sectional_ml": lambda cfg=None: CrossSectionalMLAlpha(cfg),
    "fear_greed": lambda cfg=None: FearGreedAlpha(cfg),
    "cross_asset": lambda cfg=None: CrossAssetAlpha(cfg),
    "range_reversion": lambda cfg=None: RangeReversionAlpha(cfg),
    "rv_ratio_breakout": lambda cfg=None: RvRatioBreakoutAlpha(cfg),
    "oi_divergence": lambda cfg=None: OiDivergenceAlpha(cfg),
    "lsr_contrarian": lambda cfg=None: LsrContrarianAlpha(cfg),
    "technical_ensemble": lambda cfg=None: TechnicalEnsembleAlpha(cfg),
    "external_context": lambda cfg=None: ExternalContextAlpha(cfg),
    "macro_context": lambda cfg=None: MacroContextAlpha(cfg),
    "flow_sentiment": lambda cfg=None: FlowSentimentAlpha(cfg),
    "news_impact": lambda cfg=None: NewsImpactAlpha(cfg),
}


def list_alphas() -> list[str]:
    return sorted(ALPHA_REGISTRY.keys())


# ──────────────────────────────────────────────────────────────────
# Production / experimental classification
# ──────────────────────────────────────────────────────────────────
# Updated 2026-04-15 based on scripts/alpha_audit.py results on 8yr
# BTC hourly. An alpha graduates to PRODUCTION_READY when it meets
# ALL of:
#   1. Runs on pure OHLCV (no injected tradfi/fng/funding data).
#   2. Sharpe_net ≥ -0.6 on 72k-bar full history with realistic costs.
#   3. Max drawdown ≤ 1.0 (100%) — catastrophic blow-ups disqualify.
#   4. No CHURN (turnover < 5% per bar).

# Updated 2026-04-16: full 10-alpha deep sweep on 8yr BTC+ETH.
# Alphas classified by standalone cost-adjusted Sharpe:
#   GOOD (avg SR > 0.5):  macro_context, momentum_ensemble, kalman_trend
#   marginal (0.1-0.5):   vol_breakout, trend_breakout
#   weak (< 0.1):         technical_ensemble, external_context, range_reversion
#   HARMFUL (< 0):        mean_reversion(-1.3), rv_ratio_breakout(-0.26)
#
# Production pool = GOOD + marginal. HARMFUL demoted to experimental.
PRODUCTION_READY_ALPHAS = frozenset({
    "momentum_ensemble",    # BTC +0.62, ETH +0.69 — core trend-following
    "macro_context",        # BTC +0.95, ETH +1.04 — best standalone alpha (DXY/VIX/Gold overlay)
    "vol_breakout",         # BTC +0.34, ETH +0.27 — marginal but positive after hold_bars fix
    "funding_carry",        # v4.4 (2026-04-24): OOS avg Δ +0.55 when added to v4.3 ensemble.
                            # Standalone BTC +0.64 / ETH +0.18 / BNB +0.31. Helps most in
                            # bear/sideways regimes (2022-2024, 2026 recovery). Needs
                            # per-symbol params (see shared.alpha.FUNDING_CARRY_DEFAULTS).
})

# Auxiliary: usable but not in default ensemble (bring noise/drag).
AUXILIARY_ALPHAS = frozenset({
    "trend_breakout",       # BTC -0.06, ETH +0.42 — ETH-only value, mixed
    "technical_ensemble",   # BTC +0.18, ETH -0.02 — too low activity (8%)
    "external_context",     # BTC -0.19, ETH -0.16 — superseded by macro_context
    "range_reversion",      # active 0.0% on 8yr — regime score useful, position useless
})

# Alphas excluded from the default meta pool. Reason in comments so
# the incubator UI can surface "why".
EXPERIMENTAL_ALPHAS: dict[str, str] = {
    # DEMOTED 2026-04-30 from PRODUCTION_READY. Parked-alpha 6M re-eval (data/results/expansion_findings_2026-04-28.md):
    #   6M SR -1.82, 12M -1.36, 24M -1.07. Recent regime hostile. Does not survive cost.
    "kalman_trend": "DEMOTED 2026-04-30: 6M SR -1.82 / 12M -1.36 / 24M -1.07 across BTC/ETH/BNB. Recent regimes hostile to Kalman state-space estimator. Keep available for diagnostic/lab use; not in default ensemble.",
    # Needs derivatives/exogenous data the factory doesn't wire up.
    "carry": "requires funding_rate column in df (use funding_carry instead)",
    "funding_momentum": "REJECTED 2026-04-26: 8yr standalone backtest failed all gates on BTC/ETH/BNB. BTC SR=-0.74 DD=92%, ETH SR=-0.25, BNB SR=-0.25 OOS=-1.24. Funding-momentum hypothesis (regime persistence) does not survive — gets long during the brief positive-funding window that precedes cliff drops. Kept in registry for future param/structure variations only.",
    "cross_asset": "requires tradfi_data (DXY, Gold) via constructor",
    "mean_reversion": "HARMFUL: SR -1.49/-1.16 on 8yr BTC/ETH. Crypto MR fundamentally doesn't work",
    "rv_ratio_breakout": "HARMFUL: SR -0.34/-0.18. Realized vol ratio breakout adds drag",
    "news_impact": "FNG-only training data insufficient. Needs 3+mo real NLP-scored news to retrain. IC=0.10 on holdout.",
    "flow_sentiment": "Only 20 days derivatives history. IC=0.067 (top trader) promising. Needs 3+mo accumulation.",
    "cross_sectional_momentum": "requires multi-asset panel dict",
    "cross_sectional_ml": "requires multi-asset panel + training",
    "derivatives_alpha": "requires OI/LSR series",
    "fear_greed": "requires fng_data via constructor",
    "oi_divergence": "derivatives data only 22 days, statistically weak",
    "lsr_contrarian": "derivatives data only 22 days, statistically weak",
    "stat_arb": "requires asset_a/asset_b params + panel",
    # ML alphas that need a proper train/test split lifecycle we
    # don't manage here; run them via their dedicated training harness.
    "ml_forest": "ML alpha — needs train/refit harness, not alpha-as-function",
    "ml_meta": "ML alpha — needs train/refit harness",
    "ml_meta_alpha": "ML alpha — needs train/refit harness",
    "ml_discovery": "ML alpha — needs train/refit harness",
    # Genuinely broken (8yr backtest DD 1434%, turnover 17%/bar).
    "online_rls": "RLS numerically unstable on 8yr crypto — needs regularization fix",
}


def list_production_alphas() -> list[str]:
    return sorted(PRODUCTION_READY_ALPHAS)


def list_experimental_alphas() -> dict[str, str]:
    return dict(EXPERIMENTAL_ALPHAS)


_BLOCKED_ALPHAS: frozenset[str] = frozenset({
    "online_rls",       # numerically unstable — DD 1434%, turnover 17%/bar
    "mean_reversion",   # HARMFUL: SR -1.49/-1.16 on 8yr BTC/ETH
})


def get_alpha(
    name: str,
    config: AlphaConfig | None = None,
    *,
    allow_blocked: bool = False,
    symbol: str | None = None,
) -> Alpha:
    if name not in ALPHA_REGISTRY:
        raise KeyError(f"unknown alpha '{name}'. available: {list_alphas()}")
    if not allow_blocked and name in _BLOCKED_ALPHAS:
        raise ValueError(
            f"alpha '{name}' is blocked — known harmful/unstable. "
            f"Use allow_blocked=True to override. Reason: {EXPERIMENTAL_ALPHAS.get(name, 'blocked')}"
        )
    factory = ALPHA_REGISTRY[name]
    # Some alphas (e.g. funding_carry) take a `symbol` kwarg to load
    # per-symbol external data. Pass it through if the factory accepts it.
    try:
        import inspect
        params = inspect.signature(factory).parameters
        if "symbol" in params and symbol is not None:
            return factory(config, symbol=symbol)
    except (TypeError, ValueError):
        pass
    return factory(config)


# ──────────────────────────────────────────────────────────────────
# Plugin loader (open-core seam)
# ──────────────────────────────────────────────────────────────────
# The built-in alpha catalogue above is the *default* registration. External
# (private) repositories — e.g. a `quant-alpha` repo holding proprietary alpha
# implementations — can extend the registry without forking this module by
# pointing QUANT_ALPHA_PLUGINS at one or more importable modules whose import
# side-effect calls register_alpha(...).
#
#   QUANT_ALPHA_PLUGINS=quant_alpha.alphas,my_alphas.experimental
#
# Plugin modules typically do, at import time:
#   from shared.alpha.registry import register_alpha
#   from .my_alpha import MyAlpha
#   register_alpha("my_alpha", lambda cfg=None: MyAlpha(cfg))


def register_alpha(name: str, factory: Callable[..., Alpha]) -> None:
    """Register an alpha plugin from outside this module.

    Idempotent — re-registering the same name overwrites the previous factory.
    """
    ALPHA_REGISTRY[name] = factory


def load_plugins() -> None:
    """Discover and load alpha plugins via QUANT_ALPHA_PLUGINS env.

    Failures are warned but non-fatal — a missing plugin should not bring
    the service down at boot; downstream code that needs the alpha will
    surface a clearer KeyError when get_alpha() is called.
    """
    import importlib
    import logging
    import os

    log = logging.getLogger(__name__)
    plugins = os.environ.get("QUANT_ALPHA_PLUGINS", "")
    for mod_name in plugins.split(","):
        mod_name = mod_name.strip()
        if not mod_name:
            continue
        try:
            importlib.import_module(mod_name)
            log.info("alpha_plugin_loaded", extra={"module": mod_name})
        except Exception as exc:
            log.warning(
                "alpha_plugin_load_failed",
                extra={"module": mod_name, "error": str(exc)[:200]},
            )


# Discover plugins at import time so consumers see the full registry.
load_plugins()
