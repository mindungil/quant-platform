"""Alpha health monitor.

Continuously assesses each alpha's rolling performance across multiple
time windows and provides weight-adjustment recommendations. This feeds
into the ensemble as external weight overrides, complementing the
per-bar alpha gate with a higher-level view.

Health states:
  HEALTHY:  all window Sharpes > warn threshold → weight 1.0
  DEGRADED: shortest window Sharpe < warn → weight reduced (0.5)
  CRITICAL: Sharpe < critical threshold → weight 0.0 (full kill)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from shared.backtest.metrics import sharpe_ratio


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass
class AlphaHealth:
    name: str
    status: HealthStatus
    sharpes: dict[str, float]          # window_label → sharpe
    correlations: dict[str, float]     # other_alpha_name → correlation
    turnover: float                    # mean |Δpos| per bar
    recommended_weight: float          # 0.0 - 1.0


class AlphaHealthMonitor:
    def __init__(
        self,
        windows_hours: list[int] | None = None,
        sharpe_warn: float = 0.0,
        sharpe_critical: float = -0.3,
        weight_reduction: float = 0.5,
        ppy: int = 24 * 365,
    ):
        self.windows = windows_hours or [168, 720, 2160]
        self.sharpe_warn = sharpe_warn
        self.sharpe_critical = sharpe_critical
        self.weight_reduction = weight_reduction
        self.ppy = ppy

    def assess(
        self,
        alpha_positions: dict[str, pd.Series],
        underlying_returns: pd.Series,
    ) -> dict[str, AlphaHealth]:
        """Assess health of all alphas from their position and return series."""
        results = {}
        # Compute per-alpha PnL series
        alpha_pnls = {}
        for name, pos in alpha_positions.items():
            pnl = (pos * underlying_returns).fillna(0.0)
            alpha_pnls[name] = pnl

        names = list(alpha_positions.keys())
        for name in names:
            pnl = alpha_pnls[name].values
            pos = alpha_positions[name].values

            # Rolling Sharpe at each window
            sharpes = {}
            shortest_sharpe = np.inf
            for w in self.windows:
                n = min(w, len(pnl))
                sh = float(sharpe_ratio(pnl[-n:], periods_per_year=self.ppy))
                label = f"{w}h"
                sharpes[label] = round(sh, 4)
                if w == self.windows[0]:
                    shortest_sharpe = sh

            # Correlation with other alphas
            corrs = {}
            for other in names:
                if other == name:
                    continue
                other_pnl = alpha_pnls[other].values
                n = min(720, len(pnl), len(other_pnl))
                if n > 20:
                    corrs[other] = round(float(np.corrcoef(pnl[-n:], other_pnl[-n:])[0, 1]), 4)

            # Turnover
            turnover = float(np.abs(np.diff(pos, prepend=0.0)).mean())

            # Status classification
            if shortest_sharpe < self.sharpe_critical:
                status = HealthStatus.CRITICAL
                weight = 0.0
            elif shortest_sharpe < self.sharpe_warn:
                status = HealthStatus.DEGRADED
                weight = self.weight_reduction
            else:
                status = HealthStatus.HEALTHY
                weight = 1.0

            results[name] = AlphaHealth(
                name=name,
                status=status,
                sharpes=sharpes,
                correlations=corrs,
                turnover=round(turnover, 6),
                recommended_weight=weight,
            )

        return results
