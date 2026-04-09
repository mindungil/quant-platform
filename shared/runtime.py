from __future__ import annotations

import os
from dataclasses import dataclass


class RuntimeDependencyError(RuntimeError):
    pass


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def strict_runtime_enabled() -> bool:
    return env_bool("STRICT_RUNTIME", False)


def require_env(name: str, *, allow_empty: bool = False) -> str:
    value = os.getenv(name)
    if value is None or (not allow_empty and not value.strip()):
        raise RuntimeDependencyError(f"missing_required_env:{name}")
    return value


def validate_required_env(names: list[str]) -> None:
    for name in names:
        require_env(name)


@dataclass(frozen=True)
class RuntimeFlags:
    strict_runtime: bool
    live_trading_enabled: bool
    default_shadow_mode: bool
    allowed_live_exchanges: tuple[str, ...]


def runtime_flags() -> RuntimeFlags:
    exchanges = tuple(
        item.strip().lower()
        for item in os.getenv("ALLOWED_LIVE_EXCHANGES", "binance").split(",")
        if item.strip()
    )
    return RuntimeFlags(
        strict_runtime=strict_runtime_enabled(),
        live_trading_enabled=env_bool("LIVE_TRADING_ENABLED", False),
        default_shadow_mode=env_bool("DEFAULT_SHADOW_MODE", True),
        allowed_live_exchanges=exchanges,
    )
