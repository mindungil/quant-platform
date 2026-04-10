"""Alpha registry — single source of truth for available alphas.

The strategy-registry persists strategies that name an alpha here plus an
override params dict. Backtest, shadow, and live execution all dispatch
through this registry.
"""
from __future__ import annotations

from typing import Callable

from shared.alpha.base import Alpha, AlphaConfig
from shared.alpha.carry import CarryAlpha
from shared.alpha.cross_sectional import CrossSectionalMomentumAlpha
from shared.alpha.kalman_trend import KalmanTrendAlpha
from shared.alpha.mean_reversion import MeanReversionAlpha
from shared.alpha.ml_forest import MetaForestAlpha
from shared.alpha.ml_meta import MetaMLAlpha
from shared.alpha.online_rls import OnlineRLSAlpha
from shared.alpha.order_flow import OrderFlowAlpha
from shared.alpha.lead_lag import LeadLagAlpha
from shared.alpha.vwap_reversion import VWAPReversionAlpha
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha
from shared.alpha.stat_arb import StatArbAlpha
from shared.alpha.trend_breakout import TrendBreakoutAlpha
from shared.alpha.vol_breakout import VolBreakoutAlpha


# Each entry: name -> factory(config) -> Alpha
ALPHA_REGISTRY: dict[str, Callable[[AlphaConfig | None], Alpha]] = {
    "trend_breakout": lambda cfg=None: TrendBreakoutAlpha(cfg),
    "mean_reversion": lambda cfg=None: MeanReversionAlpha(cfg),
    "momentum_ensemble": lambda cfg=None: MomentumEnsembleAlpha(cfg),
    "vol_breakout": lambda cfg=None: VolBreakoutAlpha(cfg),
    "carry": lambda cfg=None: CarryAlpha(cfg),
    "stat_arb": lambda cfg=None: StatArbAlpha(cfg),
    "cross_sectional_momentum": lambda cfg=None: CrossSectionalMomentumAlpha(cfg),
    "ml_meta": lambda cfg=None: MetaMLAlpha(cfg),
    "kalman_trend": lambda cfg=None: KalmanTrendAlpha(cfg),
    "ml_forest": lambda cfg=None: MetaForestAlpha(cfg),
    "online_rls": lambda cfg=None: OnlineRLSAlpha(cfg),
    # v4 alphas — DISABLED from default ensemble after 8-year eval showed
    # strongly negative standalone Sharpe (order_flow -4.37, vwap_reversion -3.31,
    # lead_lag -2.10). Code preserved for future debugging/improvement.
    # "order_flow": lambda cfg=None: OrderFlowAlpha(cfg),
    # "lead_lag": lambda cfg=None: LeadLagAlpha(cfg),
    # "vwap_reversion": lambda cfg=None: VWAPReversionAlpha(cfg),
}


def list_alphas() -> list[str]:
    return sorted(ALPHA_REGISTRY.keys())


def get_alpha(name: str, config: AlphaConfig | None = None) -> Alpha:
    if name not in ALPHA_REGISTRY:
        raise KeyError(f"unknown alpha '{name}'. available: {list_alphas()}")
    return ALPHA_REGISTRY[name](config)
