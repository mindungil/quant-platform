"""Signal-service compliance wiring.

Wraps shared.execution.compliance with a StateProvider backed by
portfolio-service. If portfolio-service is unreachable the provider
falls back to a conservative stub (zero positions, equity from env)
so the gateway still renders a decision rather than 500ing.

A signal's projected notional is evaluated pre-trade: if blocked,
downstream (order-service) skips execution. The signal itself is
still returned to the caller with the compliance verdict attached,
so UI/logs show *why* an order didn't fire.
"""
from __future__ import annotations

import os
import threading

import httpx

from shared.internal_admin import build_internal_admin_headers
from shared.execution.compliance import (
    ComplianceDecision,
    ComplianceGateway,
    ComplianceLimits,
    StateProvider,
)


class PortfolioStateProvider(StateProvider):
    """Fetch equity + positions from portfolio-service with short cache."""

    def __init__(self, portfolio_url: str, timeout: float = 2.0, cache_ttl: float = 5.0):
        self._url = portfolio_url.rstrip("/")
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._lock = threading.Lock()
        self._cache_ts = 0.0
        self._equity = float(os.getenv("FALLBACK_EQUITY_USD", "10000"))
        self._positions: dict[str, float] = {}
        self._kill = False

    def _refresh(self) -> None:
        import time
        now = time.monotonic()
        if now - self._cache_ts < self._cache_ttl:
            return
        try:
            r = httpx.get(
                f"{self._url}/portfolio/summary",
                headers=build_internal_admin_headers(
                    os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret"),
                    "signal-service",
                    "/portfolio/summary",
                ),
                timeout=self._timeout,
            )
            if r.status_code == 200:
                data = r.json()
                self._equity = float(data.get("equity", self._equity))
                pos = data.get("positions") or {}
                if isinstance(pos, list):
                    pos = {row["asset"]: float(row.get("notional", 0.0)) for row in pos}
                self._positions = {k: float(v) for k, v in pos.items()}
                self._kill = bool(data.get("kill_switch", False))
        except Exception:
            # Keep last-good values; conservative = treat as still-valid
            pass
        self._cache_ts = now

    def get_equity(self) -> float:
        self._refresh()
        return self._equity

    def get_positions(self) -> dict[str, float]:
        self._refresh()
        return dict(self._positions)

    def is_kill_switch_active(self) -> bool:
        self._refresh()
        return self._kill


def _limits_from_env() -> ComplianceLimits:
    def _f(name, default): return float(os.getenv(name, default))
    return ComplianceLimits(
        max_gross_leverage=_f("COMPLIANCE_MAX_GROSS_LEV", 3.0),
        max_net_exposure=_f("COMPLIANCE_MAX_NET_EXP", 1.0),
        max_symbol_weight=_f("COMPLIANCE_MAX_SYMBOL_W", 0.30),
        max_rolling_turnover=_f("COMPLIANCE_MAX_TURNOVER", 5.0),
        max_order_notional=_f("COMPLIANCE_MAX_ORDER_USD", 100_000.0),
        min_order_notional=_f("COMPLIANCE_MIN_ORDER_USD", 10.0),
        max_order_qty_pct=_f("COMPLIANCE_MAX_ORDER_PCT", 0.10),
    )


def _user_limits(user_id: str | None) -> ComplianceLimits:
    """Look up per-user compliance overrides from Redis.

    Key: compliance:limits:{user_id} → JSON of limit fields to override.
    Falls back to global env-based defaults.
    """
    base = _limits_from_env()
    if not user_id:
        return base
    try:
        import json
        from shared.persistence import RedisStore
        url = os.getenv("REDIS_URL", "")
        if not url:
            return base
        raw = RedisStore(url).get(f"compliance:limits:{user_id}")
        if raw:
            overrides = json.loads(raw) if isinstance(raw, str) else raw
            for k, v in overrides.items():
                if hasattr(base, k):
                    setattr(base, k, float(v))
    except Exception:
        pass
    return base


_gateway: ComplianceGateway | None = None


def get_gateway(user_id: str | None = None) -> ComplianceGateway:
    global _gateway
    portfolio_url = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    # Per-user limits? Create a fresh gateway (cheap — no state aside from turnover).
    if user_id:
        return ComplianceGateway(
            limits=_user_limits(user_id),
            state_provider=PortfolioStateProvider(portfolio_url),
        )
    if _gateway is None:
        _gateway = ComplianceGateway(
            limits=_limits_from_env(),
            state_provider=PortfolioStateProvider(portfolio_url),
        )
    return _gateway


def decision_to_dict(d: ComplianceDecision) -> dict:
    return {
        "approved": d.approved,
        "reason": d.reason,
        "warnings": list(d.warnings),
        "checks": dict(d.checks),
    }
