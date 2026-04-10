"""Performance attribution logger.

Logs per-bar alpha contribution, ensemble position, and rolling metrics
to disk. This provides the data foundation for health monitoring, refit
decisions, and audit trails. All output is append-only JSONL for easy
streaming analysis.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from shared.backtest.metrics import sharpe_ratio


class PerformanceLogger:
    def __init__(self, metrics_dir: str = "data/metrics"):
        self._dir = Path(metrics_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._buffer: list[dict] = []

    def log_bar(
        self,
        timestamp: str,
        symbol: str,
        alpha_positions: dict[str, float],
        alpha_weights: dict[str, float],
        target_position: float,
        underlying_return: float,
    ):
        """Log a single bar's attribution."""
        pnl = target_position * underlying_return
        alpha_contrib = {
            name: alpha_weights.get(name, 0) * alpha_positions.get(name, 0) * underlying_return
            for name in alpha_positions
        }
        self._buffer.append({
            "ts": timestamp,
            "sym": symbol,
            "pos": round(target_position, 6),
            "ret": round(underlying_return, 8),
            "pnl": round(pnl, 8),
            "alpha_pos": {k: round(v, 4) for k, v in alpha_positions.items()},
            "alpha_w": {k: round(v, 4) for k, v in alpha_weights.items()},
            "alpha_pnl": {k: round(v, 8) for k, v in alpha_contrib.items()},
        })

    def flush(self, symbol: str | None = None):
        """Write buffered records to disk."""
        if not self._buffer:
            return
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        for rec in self._buffer:
            sym = rec.get("sym", symbol or "UNKNOWN")
            sym_dir = self._dir / sym
            sym_dir.mkdir(exist_ok=True)
            path = sym_dir / f"attribution_{today}.jsonl"
            with open(path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        self._buffer.clear()

    def load_recent(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """Load recent attribution records for a symbol."""
        sym_dir = self._dir / symbol
        if not sym_dir.exists():
            return pd.DataFrame()
        records = []
        for path in sorted(sym_dir.glob("attribution_*.jsonl"))[-days:]:
            with open(path) as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)

    def compute_rolling_metrics(
        self, symbol: str, windows_hours: list[int] | None = None, ppy: int = 24 * 365
    ) -> dict:
        """Compute rolling Sharpe per alpha and total."""
        if windows_hours is None:
            windows_hours = [168, 720, 2160]
        df = self.load_recent(symbol, days=max(w // 24 + 5 for w in windows_hours))
        if df.empty or "pnl" not in df.columns:
            return {}
        total_pnl = df["pnl"].values
        result = {"total": {}, "alphas": {}}
        for w in windows_hours:
            n = min(w, len(total_pnl))
            label = f"{w}h"
            result["total"][label] = round(float(sharpe_ratio(total_pnl[-n:], periods_per_year=ppy)), 4)
        # Per-alpha
        if "alpha_pnl" in df.columns and len(df) > 0:
            alpha_names = list(df["alpha_pnl"].iloc[0].keys()) if isinstance(df["alpha_pnl"].iloc[0], dict) else []
            for name in alpha_names:
                alpha_pnl = df["alpha_pnl"].apply(lambda x: x.get(name, 0) if isinstance(x, dict) else 0).values
                result["alphas"][name] = {}
                for w in windows_hours:
                    n = min(w, len(alpha_pnl))
                    label = f"{w}h"
                    result["alphas"][name][label] = round(float(sharpe_ratio(alpha_pnl[-n:], periods_per_year=ppy)), 4)
        return result
