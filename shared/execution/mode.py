"""Execution mode — single source of truth for how orders flow.

Order of precedence (first wins):
  1. env EXECUTION_MODE=paper|virtual|testnet|live
  2. config/execution_mode.json -> "mode"
  3. legacy env LIVE_TRADING_ENABLED=true → "live", else "paper"

Always defaults to "paper" if everything is missing/invalid. The default
is intentionally the safest mode — paper bears no execution risk.

The live mode has additional safety gates checked here:
  - LIVE_TRADING_ENABLED env must explicitly be "true" (case-insensitive)
  - --confirm-live CLI flag (callers' responsibility)
  - Kill switch must be ACK'd reset (kill_switch.py guards this)

This module is import-safe: no I/O at import time. Read happens lazily
on get_execution_mode(). Result is NOT cached — callers should hold the
returned value if they need it stable across a run.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "execution_mode.json"


class ExecutionMode(str, Enum):
    PAPER = "paper"
    VIRTUAL = "virtual"
    TESTNET = "testnet"
    LIVE = "live"


@dataclass(frozen=True)
class ModeContext:
    mode: ExecutionMode
    source: str                 # "env" | "config_file" | "legacy_env" | "default"
    safety: dict[str, Any]


def _read_config_file() -> dict[str, Any] | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("execution_mode config unreadable (%s) — falling back", e)
        return None


def _coerce(raw: str | None) -> ExecutionMode | None:
    if raw is None:
        return None
    raw = raw.strip().lower()
    try:
        return ExecutionMode(raw)
    except ValueError:
        return None


def get_execution_mode() -> ModeContext:
    """Resolve current execution mode + return context object."""
    safety: dict[str, Any] = {}
    cfg = _read_config_file()
    if cfg:
        safety = cfg.get("live_safety", {})

    # 1. Explicit env wins
    env_mode = _coerce(os.getenv("EXECUTION_MODE"))
    if env_mode is not None:
        return ModeContext(mode=env_mode, source="env", safety=safety)

    # 2. Config file
    if cfg:
        cfg_mode = _coerce(cfg.get("mode"))
        if cfg_mode is not None:
            return ModeContext(mode=cfg_mode, source="config_file", safety=safety)

    # 3. Legacy env
    if os.getenv("LIVE_TRADING_ENABLED", "").lower() == "true":
        return ModeContext(mode=ExecutionMode.LIVE, source="legacy_env", safety=safety)

    # 4. Default
    return ModeContext(mode=ExecutionMode.PAPER, source="default", safety=safety)


def assert_live_safe(*, confirm_flag: bool) -> None:
    """Hard preflight before any live order routing. Raises on violation.

    Callers MUST invoke this immediately before mounting a live connector.
    """
    ctx = get_execution_mode()
    if ctx.mode != ExecutionMode.LIVE:
        return  # non-live modes don't need preflight

    # 1. Belt-and-suspenders env check
    if os.getenv("LIVE_TRADING_ENABLED", "").lower() != "true":
        raise RuntimeError(
            "live mode rejected — LIVE_TRADING_ENABLED env must be 'true'. "
            "This double-gate prevents accidental promotion via config edit."
        )

    # 2. Per-invocation confirm flag (CLI --confirm-live)
    if ctx.safety.get("require_confirm_flag", True) and not confirm_flag:
        raise RuntimeError(
            "live mode rejected — caller did not pass confirm_flag=True. "
            "Pass --confirm-live (or equivalent) to proceed."
        )

    # 3. Kill switch must not be in panic state
    try:
        from shared.risk.kill_switch import is_kill_switch_active
    except ImportError:
        return  # kill_switch module optional in some test contexts
    active, level = is_kill_switch_active()
    if active and level == "PANIC":
        raise RuntimeError(
            "live mode rejected — kill switch is in PANIC state. "
            "Operator must explicitly reset before live trading resumes."
        )


def is_paper() -> bool:
    return get_execution_mode().mode == ExecutionMode.PAPER


def is_live() -> bool:
    return get_execution_mode().mode == ExecutionMode.LIVE


def get_ramp_factor() -> float:
    """Read ramp factor from config (clamped to [0, 1]). Returns 0.0 in
    non-live modes — callers should multiply this into position sizes
    only after confirming is_live(). Hot-read on every call so the ramp
    controller can adjust without restart.
    """
    cfg = _read_config_file() or {}
    if not is_live():
        return 0.0
    raw = ((cfg.get("ramp") or {}).get("factor"))
    try:
        v = float(raw if raw is not None else 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def describe() -> str:
    """Human-readable mode banner for log headers."""
    ctx = get_execution_mode()
    extra = ""
    if ctx.mode == ExecutionMode.LIVE:
        extra = f" ramp={get_ramp_factor():.2f}"
    return f"EXECUTION_MODE={ctx.mode.value.upper()} (via {ctx.source}){extra}"
