"""Pre-trade compliance gateway.

Every order passes through this layer before hitting the exchange. Checks
are institutional-grade basics:

  - gross leverage ≤ limit
  - net exposure ≤ limit
  - per-symbol concentration ≤ limit
  - rolling turnover ≤ limit (prevents runaway churn)
  - kill-switch active? (set by ops or by drawdown overlay)
  - order notional sanity bounds (no fat-fingers)

The gateway is **stateful** — it tracks running exposure and turnover via
an injected state provider so live positions are reflected.

Returns a `ComplianceDecision` — approved/blocked + rationale, metrics-
friendly. Order-service integrates by calling `check()` before submit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol
import time


@dataclass
class ComplianceLimits:
    max_gross_leverage: float = 3.0      # Σ|pos| / equity
    max_net_exposure: float = 1.0        # |Σ pos| / equity
    max_symbol_weight: float = 0.30      # |pos_i| / equity
    max_rolling_turnover: float = 5.0    # Σ|trades|/equity per window
    turnover_window_sec: float = 3600.0  # 1h rolling
    max_order_notional: float = 100_000.0
    min_order_notional: float = 10.0
    max_order_qty_pct: float = 0.10      # single order ≤ 10% of equity


class StateProvider(Protocol):
    def get_equity(self) -> float: ...
    def get_positions(self) -> dict[str, float]: ...  # signed notional per symbol
    def is_kill_switch_active(self) -> bool: ...


@dataclass
class ComplianceDecision:
    approved: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, float] = field(default_factory=dict)


class ComplianceGateway:
    """Pre-trade compliance checks with a rolling turnover window."""

    def __init__(
        self,
        limits: ComplianceLimits,
        state_provider: StateProvider,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._limits = limits
        self._state = state_provider
        self._clock = clock
        # (timestamp, notional) pairs
        self._turnover_events: list[tuple[float, float]] = []

    # ------------------------------------------------------------------

    def _rolling_turnover(self, equity: float) -> float:
        now = self._clock()
        cutoff = now - self._limits.turnover_window_sec
        self._turnover_events = [
            (t, v) for t, v in self._turnover_events if t >= cutoff
        ]
        return sum(abs(v) for _, v in self._turnover_events) / max(equity, 1e-9)

    def record_fill(self, notional: float) -> None:
        self._turnover_events.append((self._clock(), notional))

    # ------------------------------------------------------------------

    def check(
        self,
        symbol: str,
        side: str,
        order_notional: float,
    ) -> ComplianceDecision:
        """Return an approve/block decision with diagnostics."""
        decision = ComplianceDecision(approved=True, reason="ok")
        L = self._limits

        if self._state.is_kill_switch_active():
            return ComplianceDecision(approved=False, reason="kill_switch_active")

        equity = max(self._state.get_equity(), 1e-9)
        positions = self._state.get_positions()
        signed = order_notional if side.upper() == "BUY" else -order_notional

        # Order notional sanity
        if abs(order_notional) < L.min_order_notional:
            return ComplianceDecision(
                approved=False,
                reason="order_below_min_notional",
                checks={"notional": order_notional, "min": L.min_order_notional},
            )
        if abs(order_notional) > L.max_order_notional:
            return ComplianceDecision(
                approved=False,
                reason="order_above_max_notional",
                checks={"notional": order_notional, "max": L.max_order_notional},
            )
        if abs(order_notional) / equity > L.max_order_qty_pct:
            return ComplianceDecision(
                approved=False,
                reason="order_too_large_pct_equity",
                checks={"pct": abs(order_notional) / equity, "cap": L.max_order_qty_pct},
            )

        # Projected exposure after this order
        projected = dict(positions)
        projected[symbol] = projected.get(symbol, 0.0) + signed
        gross = sum(abs(v) for v in projected.values()) / equity
        net = abs(sum(projected.values())) / equity
        symbol_w = abs(projected.get(symbol, 0.0)) / equity

        decision.checks.update({
            "gross_leverage": round(gross, 4),
            "net_exposure": round(net, 4),
            "symbol_weight": round(symbol_w, 4),
        })

        if gross > L.max_gross_leverage:
            return ComplianceDecision(
                approved=False,
                reason="gross_leverage_exceeded",
                checks=decision.checks,
            )
        if net > L.max_net_exposure:
            return ComplianceDecision(
                approved=False,
                reason="net_exposure_exceeded",
                checks=decision.checks,
            )
        if symbol_w > L.max_symbol_weight:
            return ComplianceDecision(
                approved=False,
                reason="symbol_concentration_exceeded",
                checks=decision.checks,
            )

        # Rolling turnover (include hypothetical fill)
        turnover = self._rolling_turnover(equity) + abs(order_notional) / equity
        decision.checks["rolling_turnover"] = round(turnover, 4)
        if turnover > L.max_rolling_turnover:
            return ComplianceDecision(
                approved=False,
                reason="turnover_limit_exceeded",
                checks=decision.checks,
            )

        # Soft warnings (approved but flagged)
        if gross > 0.8 * L.max_gross_leverage:
            decision.warnings.append("gross_leverage_near_limit")
        if turnover > 0.8 * L.max_rolling_turnover:
            decision.warnings.append("turnover_near_limit")

        return decision
