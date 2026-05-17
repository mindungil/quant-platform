"""Capital Tier Rollup — paper → micro → small → mid → full ramp policy.

Why this exists
---------------
Paper soak proves the strategy doesn't crash. But going from paper to
$10k live in one step skips every operational lesson — venue API quirks,
slippage at real scale, regulatory edge cases. Walking up in tiers gives
each layer a chance to surface bugs before they cost real money.

Tiers (default ladder)
----------------------
  PAPER  : max $0.01/order, $0.10 daily — pure simulator
  MICRO  : max $10/order, $100 daily — real venue, dust-level capital
  SMALL  : max $100/order, $1,000 daily — first real-money checkpoint
  MID    : max $1,000/order, $10,000 daily — production
  FULL   : max $10,000/order, unbounded daily — institutional

Promotion policy
----------------
A tier promotes to the next when ALL these hold over the last 24h on the
*current* tier:
  • Realized Sharpe ≥ promote_sharpe (default 1.0)
  • Drawdown ≤ promote_max_dd (default 5%)
  • No HARD-kill events from risk_monitor_hub
  • At least min_trades_for_promotion (default 50) trades

Manual override via CAPITAL_TIER env var (e.g. CAPITAL_TIER=SMALL) takes
absolute precedence — operator can pin a tier during incident response
or audit.

Integration
-----------
The execution layer queries `current_tier()` to know the active cap, and
`max_order_notional()` / `max_daily_notional()` to gate orders. The
`should_promote()` / `should_demote()` predicates feed an incubator
daemon that flips the active tier with `set_active_tier()`.

Risk hub integration: any HARD kill from shared.risk.monitor_hub forces
the active tier to PAPER until cleared.

Pure module, deterministic — tier transitions are explicit operator
calls (or daemon-mediated) not background magic.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Literal, Optional


TierName = Literal["PAPER", "MICRO", "SMALL", "MID", "FULL"]
_TIER_ORDER: tuple[TierName, ...] = ("PAPER", "MICRO", "SMALL", "MID", "FULL")

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Tier definition
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TierSpec:
    name: TierName
    max_order_notional_usd: float
    max_daily_notional_usd: float
    # Promotion criteria for moving UP from this tier
    promote_min_trades: int = 50
    promote_min_sharpe: float = 1.0
    promote_max_dd: float = 0.05
    # Demotion criteria for moving DOWN to the previous tier
    demote_sharpe: float = -0.5
    demote_max_dd: float = 0.10


DEFAULT_TIERS: dict[TierName, TierSpec] = {
    "PAPER": TierSpec("PAPER", 0.01, 0.10),
    "MICRO": TierSpec("MICRO", 10.0, 100.0),
    "SMALL": TierSpec("SMALL", 100.0, 1_000.0),
    "MID":   TierSpec("MID",   1_000.0, 10_000.0),
    "FULL":  TierSpec("FULL",  10_000.0, 1_000_000.0),
}


# ──────────────────────────────────────────────────────────────────
# Active tier state (process-local; persist to Redis externally)
# ──────────────────────────────────────────────────────────────────


_lock = threading.Lock()
_active_tier: TierName = "PAPER"
_tiers: dict[TierName, TierSpec] = dict(DEFAULT_TIERS)
_forced_kill = False  # set by risk_monitor_hub HARD kill


def _read_env_override() -> Optional[TierName]:
    raw = os.environ.get("CAPITAL_TIER", "").strip().upper()
    if raw in _TIER_ORDER:
        return raw  # type: ignore[return-value]
    return None


def current_tier() -> TierName:
    """Effective tier — env override > HARD kill > runtime setting."""
    with _lock:
        env = _read_env_override()
        if env:
            return env
        if _forced_kill:
            return "PAPER"
        return _active_tier


def set_active_tier(tier: TierName, *, reason: str = "manual") -> None:
    """Move the runtime tier. Env override still wins on read."""
    global _active_tier
    if tier not in _TIER_ORDER:
        raise ValueError(f"unknown tier: {tier}")
    with _lock:
        old = _active_tier
        _active_tier = tier
    logger.info("capital_tier_set", extra={"old": old, "new": tier, "reason": reason})


def register_kill_from_risk_hub() -> None:
    """Called by risk_monitor_hub on HARD kill → forces PAPER until cleared."""
    global _forced_kill
    with _lock:
        _forced_kill = True
    logger.warning("capital_tier_forced_to_paper_by_kill")


def clear_kill() -> None:
    global _forced_kill
    with _lock:
        _forced_kill = False
    logger.info("capital_tier_kill_cleared")


# ──────────────────────────────────────────────────────────────────
# Tier-aware sizing
# ──────────────────────────────────────────────────────────────────


def current_spec() -> TierSpec:
    return _tiers[current_tier()]


def max_order_notional() -> float:
    return current_spec().max_order_notional_usd


def max_daily_notional() -> float:
    return current_spec().max_daily_notional_usd


def cap_order_notional(requested: float) -> float:
    """Return min(requested, tier cap). Use this in _calculate_position_size."""
    return min(max(requested, 0.0), max_order_notional())


# ──────────────────────────────────────────────────────────────────
# Promotion / demotion logic
# ──────────────────────────────────────────────────────────────────


@dataclass
class TierStats:
    """24h roll-up. Feed from shadow_fills or live ledger."""

    n_trades: int = 0
    realized_sharpe: float = 0.0
    realized_max_dd: float = 0.0
    hard_kill_events: int = 0


def should_promote(stats: TierStats, *, current: Optional[TierName] = None) -> bool:
    """True iff stats meet the promotion criteria of `current` tier AND
    a next tier exists."""
    tier = current or current_tier()
    idx = _TIER_ORDER.index(tier)
    if idx >= len(_TIER_ORDER) - 1:
        return False
    spec = _tiers[tier]
    return (
        stats.n_trades >= spec.promote_min_trades
        and stats.realized_sharpe >= spec.promote_min_sharpe
        and stats.realized_max_dd <= spec.promote_max_dd
        and stats.hard_kill_events == 0
    )


def should_demote(stats: TierStats, *, current: Optional[TierName] = None) -> bool:
    """True iff stats trigger demotion AND a previous tier exists."""
    tier = current or current_tier()
    idx = _TIER_ORDER.index(tier)
    if idx == 0:
        return False
    spec = _tiers[tier]
    return (
        stats.hard_kill_events > 0
        or stats.realized_sharpe <= spec.demote_sharpe
        or stats.realized_max_dd >= spec.demote_max_dd
    )


def next_tier(tier: TierName) -> Optional[TierName]:
    idx = _TIER_ORDER.index(tier)
    return _TIER_ORDER[idx + 1] if idx + 1 < len(_TIER_ORDER) else None


def prev_tier(tier: TierName) -> Optional[TierName]:
    idx = _TIER_ORDER.index(tier)
    return _TIER_ORDER[idx - 1] if idx > 0 else None


def evaluate_tier_transition(stats: TierStats) -> Optional[TierName]:
    """Return the tier to switch to, or None to stay. Demotion wins over
    promotion when both fire (defensive)."""
    if should_demote(stats):
        return prev_tier(current_tier())
    if should_promote(stats):
        return next_tier(current_tier())
    return None


def snapshot() -> dict:
    """Operator-facing state — for /risk/capital-tier endpoint."""
    return {
        "active_tier": current_tier(),
        "env_override": _read_env_override(),
        "forced_kill": _forced_kill,
        "max_order_notional": max_order_notional(),
        "max_daily_notional": max_daily_notional(),
        "tiers": {n: vars(s) for n, s in _tiers.items()},
    }
