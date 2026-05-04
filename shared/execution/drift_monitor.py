"""Realized vs expected execution drift monitor.

After each live fill, compare the actual slippage (bps vs arrival mid)
against what `impact_model.estimate_impact` predicted pre-trade. Keep a
rolling EWMA of the ratio. When it exceeds `drift_ratio_kill` for
`persistence_fills` consecutive fills, emit a recalibration alert —
the book depth / spread model is stale and k_sqrt needs refitting.

Does NOT auto-disable trading on its own (that's the kill switch's job);
drift is a hint to operators + a nudge to widen the per-slice impact
budget until recal completes.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_LOG = Path("data/logs/drift_monitor.jsonl")


@dataclass
class DriftConfig:
    ewma_alpha: float = 0.2                # fill-level EWMA
    drift_ratio_warn: float = 1.5
    drift_ratio_kill: float = 3.0          # actual > 3× predicted = recalibration
    persistence_fills: int = 10


@dataclass
class DriftStats:
    n_fills: int = 0
    ewma_ratio: float = 1.0
    ewma_pred_bps: float = 0.0
    ewma_actual_bps: float = 0.0
    bad_streak: int = 0
    alert_level: str = "ok"                 # ok | warn | recalibrate


def _log(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


class DriftMonitor:
    """Stateful monitor — one instance per (symbol, side) or pooled."""

    def __init__(
        self,
        config: DriftConfig | None = None,
        log_path: Path | str | None = None,
    ) -> None:
        self.config = config or DriftConfig()
        self.stats = DriftStats()
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG

    def record_fill(
        self,
        *,
        predicted_bps: float,
        actual_bps: float,
        symbol: str = "",
        side: str = "",
    ) -> DriftStats:
        """Ingest one fill and return updated stats."""
        self.stats.n_fills += 1
        a = self.config.ewma_alpha
        self.stats.ewma_pred_bps = (1 - a) * self.stats.ewma_pred_bps + a * predicted_bps
        self.stats.ewma_actual_bps = (1 - a) * self.stats.ewma_actual_bps + a * actual_bps

        ratio = 1.0
        if self.stats.ewma_pred_bps > 0.01:
            ratio = self.stats.ewma_actual_bps / self.stats.ewma_pred_bps
        self.stats.ewma_ratio = round(float(ratio), 3)

        cfg = self.config
        if ratio >= cfg.drift_ratio_kill:
            self.stats.bad_streak += 1
        else:
            self.stats.bad_streak = 0

        if self.stats.bad_streak >= cfg.persistence_fills:
            self.stats.alert_level = "recalibrate"
        elif ratio >= cfg.drift_ratio_warn:
            self.stats.alert_level = "warn"
        else:
            self.stats.alert_level = "ok"

        _log(
            self.log_path,
            {
                "ts": time.time(),
                "symbol": symbol,
                "side": side,
                "predicted_bps": round(float(predicted_bps), 3),
                "actual_bps": round(float(actual_bps), 3),
                "ratio": self.stats.ewma_ratio,
                "level": self.stats.alert_level,
            },
        )
        return self.stats

    def snapshot(self) -> dict:
        return asdict(self.stats)
