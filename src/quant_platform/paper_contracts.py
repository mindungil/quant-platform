"""Immutable contracts for deterministic single-strategy paper sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .contracts import MarketBar, Signal
from .risk_engine import KillSwitchEvent, RiskCheckpoint, RiskDecisionRecord
from .strategy_decision import (
    StrategyDecisionOutcome,
    StrategyDecisionPackage,
    StrategyLifecycleState,
)
from .venue_simulator import VenueFillEvidence, VenueOrderRecord, VenueQuote

ZERO = Decimal("0")


class PaperCycleStatus(StrEnum):
    NO_ACTION = "NO_ACTION"
    RISK_REJECTED = "RISK_REJECTED"
    BELOW_VENUE_INCREMENT = "BELOW_VENUE_INCREMENT"
    VENUE_REJECTED = "VENUE_REJECTED"
    NO_FILL = "NO_FILL"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    POST_TRADE_BLOCKED = "POST_TRADE_BLOCKED"


@dataclass(frozen=True, slots=True)
class PaperLaunchAuthorization:
    authorization_id: str
    session_id: str
    strategy_id: str
    decision_package_id: str
    decision_package_sha256: str
    account_id: str
    symbol: str
    settlement_currency: str
    venue_profile_snapshot_id: str
    risk_policy_id: str
    authorized_at: datetime
    authorized_by: str
    initial_cash: Decimal
    model_version: str = "single-strategy-paper-v1"

    def __post_init__(self) -> None:
        for name in (
            "authorization_id",
            "session_id",
            "strategy_id",
            "decision_package_id",
            "decision_package_sha256",
            "account_id",
            "symbol",
            "settlement_currency",
            "venue_profile_snapshot_id",
            "risk_policy_id",
            "authorized_by",
            "model_version",
        ):
            _text(getattr(self, name), name)
        _aware(self.authorized_at, "authorized_at")
        _sha256(self.decision_package_sha256, "decision_package_sha256")
        _positive(self.initial_cash, "initial_cash")

    @classmethod
    def from_package(
        cls,
        *,
        authorization_id: str,
        session_id: str,
        package: StrategyDecisionPackage,
        account_id: str,
        symbol: str,
        settlement_currency: str,
        venue_profile_snapshot_id: str,
        risk_policy_id: str,
        authorized_at: datetime,
        authorized_by: str,
        initial_cash: Decimal,
    ) -> PaperLaunchAuthorization:
        _aware(authorized_at, "authorized_at")
        if package.state is not StrategyLifecycleState.PAPER:
            raise ValueError("paper authorization requires a package in PAPER state")
        approved = tuple(
            decision
            for decision in package.decisions
            if decision.outcome is StrategyDecisionOutcome.APPROVED
            and decision.target_state is StrategyLifecycleState.PAPER
        )
        if not approved:
            raise ValueError("paper authorization requires an approved PAPER decision")
        if authorized_at < package.updated_at:
            raise ValueError("authorized_at must not precede the package update")
        return cls(
            authorization_id=authorization_id,
            session_id=session_id,
            strategy_id=package.strategy_id,
            decision_package_id=package.package_id,
            decision_package_sha256=package.content_sha256(),
            account_id=account_id,
            symbol=symbol,
            settlement_currency=settlement_currency,
            venue_profile_snapshot_id=venue_profile_snapshot_id,
            risk_policy_id=risk_policy_id,
            authorized_at=authorized_at,
            authorized_by=authorized_by,
            initial_cash=initial_cash,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "authorization_id": self.authorization_id,
            "authorized_at": self.authorized_at.isoformat(),
            "authorized_by": self.authorized_by,
            "decision_package_id": self.decision_package_id,
            "decision_package_sha256": self.decision_package_sha256,
            "initial_cash": str(self.initial_cash),
            "model_version": self.model_version,
            "risk_policy_id": self.risk_policy_id,
            "session_id": self.session_id,
            "settlement_currency": self.settlement_currency,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "venue_profile_snapshot_id": self.venue_profile_snapshot_id,
        }


@dataclass(frozen=True, slots=True)
class PaperCycleRequest:
    cycle_id: str
    occurred_at: datetime
    completed_bars: tuple[MarketBar, ...]
    decision_quote: VenueQuote
    match_quote: VenueQuote
    daily_pnl: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        _text(self.cycle_id, "cycle_id")
        _aware(self.occurred_at, "occurred_at")
        _finite(self.daily_pnl, "daily_pnl")
        if not self.completed_bars:
            raise ValueError("completed_bars must not be empty")


@dataclass(frozen=True, slots=True)
class PaperCycleResult:
    cycle_id: str
    session_id: str
    status: PaperCycleStatus
    occurred_at: datetime
    signal: Signal
    equity_before: Decimal
    current_position_quantity: Decimal
    desired_position_quantity: Decimal
    requested_order_quantity: Decimal
    submitted_order_quantity: Decimal
    executed_quantity: Decimal
    pre_trade_decision: RiskDecisionRecord | None
    venue_order: VenueOrderRecord | None
    venue_fill: VenueFillEvidence | None
    post_trade_decision: RiskDecisionRecord | None
    kill_switch_event: KillSwitchEvent | None
    checkpoint: RiskCheckpoint
    event_sequence_start: int
    event_sequence_end: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "checkpoint": _checkpoint_dict(self.checkpoint),
            "current_position_quantity": str(self.current_position_quantity),
            "cycle_id": self.cycle_id,
            "desired_position_quantity": str(self.desired_position_quantity),
            "equity_before": str(self.equity_before),
            "event_sequence_end": self.event_sequence_end,
            "event_sequence_start": self.event_sequence_start,
            "executed_quantity": str(self.executed_quantity),
            "kill_switch": _kill_dict(self.kill_switch_event),
            "occurred_at": self.occurred_at.isoformat(),
            "post_trade": _risk_dict(self.post_trade_decision),
            "pre_trade": _risk_dict(self.pre_trade_decision),
            "reason": self.reason,
            "requested_order_quantity": str(self.requested_order_quantity),
            "session_id": self.session_id,
            "signal": {
                "generated_at": self.signal.generated_at.isoformat(),
                "score": self.signal.score,
                "source": self.signal.source,
                "symbol": self.signal.symbol,
            },
            "status": self.status.value,
            "submitted_order_quantity": str(self.submitted_order_quantity),
            "venue_fill": _fill_dict(self.venue_fill),
            "venue_order": _order_dict(self.venue_order),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _risk_dict(record: RiskDecisionRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "allowed": record.decision.allowed,
        "decision_id": record.decision_id,
        "reason": record.decision.reason,
        "sequence": record.sequence,
        "size_multiplier": record.decision.size_multiplier,
    }


def _order_dict(record: VenueOrderRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "order_id": record.request.order_id,
        "quantity": str(record.request.quantity),
        "reason": record.reason,
        "status": record.status.value,
        "violations": [item.value for item in record.violations],
    }


def _fill_dict(fill: VenueFillEvidence | None) -> dict[str, object] | None:
    if fill is None:
        return None
    return {
        "fee_amount": str(fill.fee_amount),
        "fill_id": fill.fill_id,
        "liquidity_role": fill.liquidity_role.value,
        "price": str(fill.price),
        "quantity": str(fill.quantity),
        "quote_id": fill.quote_id,
    }


def _kill_dict(event: KillSwitchEvent | None) -> dict[str, object] | None:
    if event is None:
        return None
    return {
        "event_id": event.event_id,
        "reason": event.reason,
        "sequence": event.sequence,
        "source": event.source.value,
        "state": event.state.value,
    }


def _checkpoint_dict(checkpoint: RiskCheckpoint) -> dict[str, object]:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "created_at": checkpoint.created_at.isoformat(),
        "event_log_sha256": checkpoint.event_log_sha256,
        "event_sequence": checkpoint.event_sequence,
        "state_sha256": checkpoint.state_sha256,
    }


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _positive(value: Decimal, name: str) -> None:
    _finite(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _sha256(value: str, name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{name} must be a 64-character SHA-256 digest")


__all__ = [
    "PaperCycleRequest",
    "PaperCycleResult",
    "PaperCycleStatus",
    "PaperLaunchAuthorization",
]
