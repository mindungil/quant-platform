"""Unified engine configuration.

Single source of truth for all engine parameters. Extends the v4.1
production config with refit, health monitoring, and adaptive timeframe
settings. Serializable to/from JSON for persistence and audit trail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class EngineConfig:
    """Complete engine configuration — alphas, ensemble, and self-improvement."""

    # ---- Alpha params (v4.1 deep-sweep winners) ----
    alphas: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "kalman_trend":      {"obs_var": 5e-4, "slope_var": 5e-8},
        "momentum_ensemble": {"windows": [168, 720]},
        "trend_breakout":    {"donchian_window": 120, "exit_window": 55},
    })

    # ---- Regime affinity ----
    affinity: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "kalman_trend":      {"TREND_UP": 1.4, "TREND_DOWN": 1.4, "RANGE": 0.6, "CRISIS": 0.5},
        "momentum_ensemble": {"TREND_UP": 1.4, "TREND_DOWN": 1.4, "RANGE": 0.5, "CRISIS": 0.4},
        "trend_breakout":    {"TREND_UP": 1.5, "TREND_DOWN": 1.5, "RANGE": 0.4, "CRISIS": 0.6},
    })

    # ---- Ensemble settings ----
    combine_mode: str = "equal"
    sizing_mode: str = "vol_target"
    turnover_deadzone: float = 0.10
    target_vol_annual: float = 0.20
    kill_drawdown: float = 0.20

    # ---- Rolling parameter refit ----
    refit_enabled: bool = True
    refit_lookback_days: int = 180
    refit_oos_days: int = 30
    refit_significance: float = 0.05       # p-value threshold for Welch t-test
    refit_safety_margin: float = 0.10      # candidate must beat current Sharpe by this
    refit_require_majority: bool = True    # must win on > half of symbols

    # ---- Alpha health monitor ----
    health_windows_hours: list[int] = field(default_factory=lambda: [168, 720, 2160])
    health_sharpe_warn: float = 0.0        # 7d Sharpe below this → DEGRADED
    health_sharpe_critical: float = -0.3   # → CRITICAL, weight reduced
    health_weight_reduction: float = 0.5   # multiplier when DEGRADED

    # ---- Adaptive timeframe ----
    adaptive_enabled: bool = True
    adaptive_vol_high_z: float = 0.5       # above → prefer 1h
    adaptive_vol_low_z: float = -0.5       # below → prefer 8h
    adaptive_dwell_bars: int = 12          # minimum bars before switching
    adaptive_default_tf: str = "1h"

    # ---- Symbols ----
    symbols: list[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "LINKUSDT"
    ])

    # ---- Paths ----
    data_dir: str = "data/ohlcv"
    metrics_dir: str = "data/metrics"
    config_path: str = "config/v4_production.json"
    archive_dir: str = "config/archive"


def load_config(path: str | Path) -> EngineConfig:
    """Load EngineConfig from a JSON file."""
    p = Path(path)
    if not p.exists():
        return EngineConfig()
    with open(p) as f:
        data = json.load(f)
    # Map from v4_production.json format to EngineConfig fields
    cfg = EngineConfig()
    if "alphas" in data:
        cfg.alphas = {k: v.get("params", {}) for k, v in data["alphas"].items() if v.get("enabled", True)}
    if "affinity" in data:
        cfg.affinity = data["affinity"]
    if "ensemble" in data:
        ens = data["ensemble"]
        cfg.combine_mode = ens.get("combine_mode", cfg.combine_mode)
        cfg.sizing_mode = ens.get("sizing_mode", cfg.sizing_mode)
        cfg.turnover_deadzone = ens.get("turnover_deadzone", cfg.turnover_deadzone)
        cfg.target_vol_annual = ens.get("target_vol_annual", cfg.target_vol_annual)
        cfg.kill_drawdown = ens.get("kill_drawdown", cfg.kill_drawdown)
    if "symbols" in data:
        cfg.symbols = data["symbols"]
    # Engine-specific extensions
    if "engine" in data:
        eng = data["engine"]
        for k, v in eng.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def save_config(cfg: EngineConfig, path: str | Path):
    """Save EngineConfig to JSON (extended v4 format)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "v4.1-engine",
        "alphas": {name: {"enabled": True, "params": params} for name, params in cfg.alphas.items()},
        "affinity": cfg.affinity,
        "ensemble": {
            "combine_mode": cfg.combine_mode,
            "sizing_mode": cfg.sizing_mode,
            "turnover_deadzone": cfg.turnover_deadzone,
            "target_vol_annual": cfg.target_vol_annual,
            "kill_drawdown": cfg.kill_drawdown,
        },
        "symbols": cfg.symbols,
        "engine": {
            "refit_enabled": cfg.refit_enabled,
            "refit_lookback_days": cfg.refit_lookback_days,
            "refit_oos_days": cfg.refit_oos_days,
            "refit_significance": cfg.refit_significance,
            "refit_safety_margin": cfg.refit_safety_margin,
            "health_windows_hours": cfg.health_windows_hours,
            "health_sharpe_warn": cfg.health_sharpe_warn,
            "health_sharpe_critical": cfg.health_sharpe_critical,
            "adaptive_enabled": cfg.adaptive_enabled,
            "adaptive_vol_high_z": cfg.adaptive_vol_high_z,
            "adaptive_vol_low_z": cfg.adaptive_vol_low_z,
            "adaptive_dwell_bars": cfg.adaptive_dwell_bars,
            "adaptive_default_tf": cfg.adaptive_default_tf,
        },
    }
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
