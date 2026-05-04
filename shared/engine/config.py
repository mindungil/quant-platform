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
    kelly_fraction: float = 0.5            # half-Kelly default; only used when sizing_mode == "half_kelly"
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

    # ---- Alpha mining (auto-discovery) ----
    mining_enabled: bool = True
    mining_interval_days: int = 30        # run monthly
    mining_n_candidates: int = 50
    mining_min_oos_sharpe: float = 0.3
    mining_max_corr_existing: float = 0.4

    # ---- Symbols ----
    symbols: list[str] = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"
    ])

    # ---- Per-asset alpha overrides (v4.3+) ----
    # Each entry: symbol -> {alphas: [...], params: {...}, maker_dsr, maker_verdict, note}
    # If symbol not in asset_overrides, it falls back to the global `alphas` list.
    asset_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ---- Parked symbols (no edge, force flat) ----
    # symbol -> {reason: str, last_dsr or last_sharpe: float}
    symbols_parked: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ---- Parked alphas (observation mode, not used in production) ----
    parked_alphas: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ---- Live alpha guard (6M rolling Sharpe auto-park) ----
    live_guard_enabled: bool = True
    live_guard_lookback_days: int = 180     # 6-month window
    live_guard_park_threshold: float = -0.5 # 6M Sharpe < this → auto-park
    live_guard_warn_threshold: float = 0.0  # 6M Sharpe < this → WARN (half size)
    live_guard_min_bars: int = 500          # need at least N bars for stable 6M SR

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
        cfg.kelly_fraction = ens.get("kelly_fraction", cfg.kelly_fraction)
        cfg.turnover_deadzone = ens.get("turnover_deadzone", cfg.turnover_deadzone)
        cfg.target_vol_annual = ens.get("target_vol_annual", cfg.target_vol_annual)
        cfg.kill_drawdown = ens.get("kill_drawdown", cfg.kill_drawdown)
    if "symbols" in data:
        cfg.symbols = data["symbols"]
    if "asset_overrides" in data:
        cfg.asset_overrides = data["asset_overrides"]
    if "symbols_parked" in data:
        cfg.symbols_parked = data["symbols_parked"]
    if "parked_alphas" in data:
        cfg.parked_alphas = data["parked_alphas"]
    if "live_guard" in data:
        lg = data["live_guard"]
        cfg.live_guard_enabled = lg.get("enabled", cfg.live_guard_enabled)
        cfg.live_guard_lookback_days = lg.get("lookback_days", cfg.live_guard_lookback_days)
        cfg.live_guard_park_threshold = lg.get("park_threshold", cfg.live_guard_park_threshold)
        cfg.live_guard_warn_threshold = lg.get("warn_threshold", cfg.live_guard_warn_threshold)
        cfg.live_guard_min_bars = lg.get("min_bars", cfg.live_guard_min_bars)
    # Engine-specific extensions
    if "engine" in data:
        eng = data["engine"]
        for k, v in eng.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


# ──────────────────────────────────────────────────────────────────
# Live-guard helpers
# ──────────────────────────────────────────────────────────────────

def alphas_for_symbol(cfg: EngineConfig, symbol: str) -> tuple[list[str], dict[str, dict]]:
    """Return (enabled_alpha_names, per_alpha_params) for a given symbol.

    Priority:
      1. If symbol is in symbols_parked → return ([], {}) — force flat.
      2. If symbol is in asset_overrides and has non-empty 'alphas' → use those.
      3. Else fall back to the global cfg.alphas.
    """
    if symbol in cfg.symbols_parked:
        return [], {}
    override = cfg.asset_overrides.get(symbol, {})
    override_alphas = override.get("alphas")
    if override_alphas is not None:
        # Explicit override — may be empty list (parked via override)
        if len(override_alphas) == 0:
            return [], {}
        params = {name: cfg.alphas.get(name, {}) for name in override_alphas}
        # Merge any per-symbol param overrides
        sym_params = override.get("params", {})
        for name, p in sym_params.items():
            if name in params:
                params[name] = {**params[name], **p}
        return list(override_alphas), params
    return list(cfg.alphas.keys()), dict(cfg.alphas)


def is_symbol_parked(cfg: EngineConfig, symbol: str) -> tuple[bool, str]:
    """Return (is_parked, reason). Config-level parking only — not live guard."""
    if symbol in cfg.symbols_parked:
        info = cfg.symbols_parked[symbol]
        return True, info.get("reason", "parked (no reason given)")
    override = cfg.asset_overrides.get(symbol, {})
    if override.get("alphas") == []:
        return True, override.get("note", "empty alpha override")
    return False, ""


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
