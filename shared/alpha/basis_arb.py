"""Spot-perp basis arbitrage alpha.

Classic crypto-native edge: when a perpetual futures contract trades
materially above (or below) its underlying spot, funding payments + the
basis itself eventually drag the prices back together. The trade is
*market-neutral by construction* — long one leg, short the other — so
PnL comes from basis convergence rather than directional moves in BTC.

This alpha:
1. Computes basis_bp = (perp_close - spot_close) / spot_close × 10_000
2. Z-scores the basis over a rolling window
3. Takes a position proportional to -tanh(z / threshold)
   - z >>  → perp expensive → SHORT basis (= short perp / long spot)  → negative `position`
   - z << → perp cheap     → LONG basis  (= long perp / short spot)  → positive `position`
   The convention: `position` ∈ [-1, 1] represents the net *perp* leg;
   the runner is responsible for placing the offsetting spot leg.

Optionally enriches the signal with a funding-rate term — if funding is
extremely positive (longs paying shorts), it strengthens the short-perp
signal. Set `params['funding_weight'] = 0` to disable.

Input frame columns required:
  spot_close, perp_close       — mandatory
  funding_rate (optional)      — periodic funding rate in raw fraction
                                  (e.g. 0.0001 = 1bp per funding interval)

Reference: Frazzini & Pedersen (2014); Hodrick & Tomunen for perp-spot
divergence; Härdle et al. (2022) on crypto basis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.alpha.base import Alpha, AlphaConfig, AlphaSignal, rolling_zscore


class BasisArbAlpha(Alpha):
    """Spot-perp basis convergence alpha."""

    requires_panel = False
    requires_exog = False
    safe_for_standalone_use = True  # market-neutral by construction

    def __init__(self, config: AlphaConfig | None = None) -> None:
        if config is None:
            config = AlphaConfig(name="basis_arb")
        super().__init__(config)
        self._zscore_window = int(config.params.get("zscore_window", 96))
        self._threshold_z = float(config.params.get("threshold_z", 1.5))
        self._funding_weight = float(config.params.get("funding_weight", 0.3))

    def _generate(self, df: pd.DataFrame) -> pd.Series:
        if not isinstance(df, pd.DataFrame):
            return pd.Series(dtype=float)
        if "spot_close" not in df.columns or "perp_close" not in df.columns:
            # Missing legs — return flat. Safe no-op when the executor
            # didn't wire up both spot and perp data sources.
            return pd.Series(0.0, index=df.index)

        spot = df["spot_close"].astype(float)
        perp = df["perp_close"].astype(float)
        # Defensive against zero spot prices
        denom = spot.replace(0, np.nan)
        basis_bp = (perp - spot) / denom * 10_000.0
        basis_bp = basis_bp.fillna(0.0)

        z = rolling_zscore(basis_bp, self._zscore_window)
        # Map z → position via tanh squashing; the threshold sets where
        # the position starts to saturate. position is the perp leg, so
        # NEGATIVE when perp is rich.
        pos = -np.tanh(z / max(self._threshold_z, 1e-6))

        # Funding overlay: if longs are paying shorts very aggressively
        # (positive funding), bias toward short-perp. Capped at funding_weight
        # so it can't override the basis signal entirely.
        if "funding_rate" in df.columns and self._funding_weight > 0:
            funding_z = rolling_zscore(
                df["funding_rate"].astype(float), self._zscore_window
            )
            funding_tilt = -np.tanh(funding_z / 2.0) * self._funding_weight
            pos = pos + funding_tilt
            pos = pos.clip(-1.0, 1.0)

        return pd.Series(pos, index=df.index).fillna(0.0)
