"""Versioned isolated-margin, financing, funding, and liquidation reference model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from .execution_engine import (
    CashFlowKind,
    CashSettlementMode,
    EventSourcedExecutionEngine,
    PositionState,
)
from .execution_profiles import ExecutionProfileSnapshot
from .finance import ExecutionOrderType, OrderSide

ZERO = Decimal("0")
ONE = Decimal("1")
SECONDS_PER_YEAR = Decimal(365 * 24 * 60 * 60)


@dataclass(frozen=True, slots=True)
class PeriodicFundingSchedule:
    schedule_id: str
    anchor_at: datetime
    interval: timedelta
    model_version: str = "periodic-funding-v1"

    def __post_init__(self) -> None:
        _text(self.schedule_id, "schedule_id")
        _aware(self.anchor_at, "anchor_at")
        if self.interval <= timedelta(0):
            raise ValueError("funding interval must be positive")
        _text(self.model_version, "model_version")

    def require_due(self, occurred_at: datetime) -> None:
        _aware(occurred_at, "occurred_at")
        elapsed = occurred_at - self.anchor_at
        if elapsed < timedelta(0) or elapsed % self.interval != timedelta(0):
            raise ValueError("funding event is not aligned with the configured schedule")


@dataclass(frozen=True, slots=True)
class IsolatedMarginProfile:
    profile_id: str
    schema_version: str
    execution_snapshot_id: str
    settlement_currency: str
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    liquidation_fee_rate: Decimal
    funding_schedule: PeriodicFundingSchedule
    borrow_rate_per_year: Decimal | None = None
    margin_interest_rate_per_year: Decimal | None = None
    model_version: str = "isolated-linear-margin-v1"

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "schema_version",
            "execution_snapshot_id",
            "settlement_currency",
            "model_version",
        ):
            _text(getattr(self, name), name)
        _unit_rate(self.initial_margin_rate, "initial_margin_rate", positive=True)
        _unit_rate(self.maintenance_margin_rate, "maintenance_margin_rate", positive=True)
        _unit_rate(self.liquidation_fee_rate, "liquidation_fee_rate")
        if self.maintenance_margin_rate > self.initial_margin_rate:
            raise ValueError("maintenance margin must not exceed initial margin")
        for name in ("borrow_rate_per_year", "margin_interest_rate_per_year"):
            value = getattr(self, name)
            if value is not None:
                _positive(value, name)


@dataclass(frozen=True, slots=True)
class VersionedVenueProfile:
    execution: ExecutionProfileSnapshot
    margin: IsolatedMarginProfile

    def __post_init__(self) -> None:
        if self.margin.execution_snapshot_id != self.execution.snapshot_id:
            raise ValueError("margin profile must reference the execution snapshot")
        if self.margin.settlement_currency != self.execution.profile.settlement_currency:
            raise ValueError("margin and execution settlement currencies must match")
        if self.execution.rules.contract_multiplier != ONE:
            raise ValueError(
                "reference isolated-margin model requires a unit contract multiplier"
            )

    @property
    def profile_key(self) -> str:
        return f"{self.execution.snapshot_id}:{self.margin.profile_id}"

    def to_json(self) -> str:
        payload = {
            "execution_snapshot_id": self.execution.snapshot_id,
            "margin_profile_id": self.margin.profile_id,
            "profile_key": self.profile_key,
            "schema_version": self.margin.schema_version,
            "model_version": self.margin.model_version,
            "settlement_currency": self.margin.settlement_currency,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class FinancingChargeKind(StrEnum):
    BORROW_INTEREST = "BORROW_INTEREST"
    MARGIN_INTEREST = "MARGIN_INTEREST"


@dataclass(frozen=True, slots=True)
class FundingEvidence:
    event_id: str
    schedule_id: str
    occurred_at: datetime
    account_id: str
    symbol: str
    position_quantity: Decimal
    mark_price: Decimal
    funding_rate: Decimal
    position_notional: Decimal
    cash_amount: Decimal
    model_version: str


@dataclass(frozen=True, slots=True)
class FinancingChargeEvidence:
    event_id: str
    kind: FinancingChargeKind
    occurred_at: datetime
    account_id: str
    currency: str
    principal: Decimal
    annual_rate: Decimal
    elapsed: timedelta
    cash_amount: Decimal
    model_version: str


@dataclass(frozen=True, slots=True)
class MarginAccountSnapshot:
    account_id: str
    symbol: str
    currency: str
    observed_at: datetime
    cash_balance: Decimal
    position_quantity: Decimal
    average_price: Decimal
    mark_price: Decimal | None
    position_notional: Decimal
    unrealized_pnl: Decimal
    equity: Decimal
    initial_margin_requirement: Decimal
    maintenance_margin_requirement: Decimal
    liquidation_fee_reserve: Decimal
    available_equity: Decimal
    margin_excess: Decimal
    liquidatable: bool
    profile_key: str

    def to_json(self) -> str:
        payload = {
            "account_id": self.account_id,
            "available_equity": str(self.available_equity),
            "average_price": str(self.average_price),
            "cash_balance": str(self.cash_balance),
            "currency": self.currency,
            "equity": str(self.equity),
            "initial_margin_requirement": str(self.initial_margin_requirement),
            "liquidatable": self.liquidatable,
            "liquidation_fee_reserve": str(self.liquidation_fee_reserve),
            "maintenance_margin_requirement": str(self.maintenance_margin_requirement),
            "margin_excess": str(self.margin_excess),
            "mark_price": None if self.mark_price is None else str(self.mark_price),
            "observed_at": self.observed_at.isoformat(),
            "position_notional": str(self.position_notional),
            "position_quantity": str(self.position_quantity),
            "profile_key": self.profile_key,
            "symbol": self.symbol,
            "unrealized_pnl": str(self.unrealized_pnl),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class LiquidationResult:
    liquidation_id: str
    occurred_at: datetime
    side: OrderSide
    quantity: Decimal
    trigger_mark_price: Decimal
    execution_price: Decimal
    liquidation_fee: Decimal
    before: MarginAccountSnapshot
    after: MarginAccountSnapshot
    model_version: str

    def to_json(self) -> str:
        payload = {
            "after": json.loads(self.after.to_json()),
            "before": json.loads(self.before.to_json()),
            "execution_price": str(self.execution_price),
            "liquidation_fee": str(self.liquidation_fee),
            "liquidation_id": self.liquidation_id,
            "model_version": self.model_version,
            "occurred_at": self.occurred_at.isoformat(),
            "quantity": str(self.quantity),
            "side": self.side.value,
            "trigger_mark_price": str(self.trigger_mark_price),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class IsolatedMarginSimulator:
    """Single-symbol linear isolated-margin reference layer.

    Cash is the settlement-currency collateral balance. Equity equals cash plus
    unrealized PnL. A position becomes liquidatable when equity is no greater
    than maintenance margin plus the configured liquidation-fee reserve.
    """

    def __init__(
        self,
        profile: VersionedVenueProfile,
        engine: EventSourcedExecutionEngine | None = None,
    ) -> None:
        self.profile = profile
        self.engine = engine or EventSourcedExecutionEngine()

    def evaluate(
        self,
        *,
        account_id: str,
        observed_at: datetime,
        mark_price: Decimal | None = None,
    ) -> MarginAccountSnapshot:
        _text(account_id, "account_id")
        _aware(observed_at, "observed_at")
        position = self._position(account_id)
        effective_mark = mark_price if mark_price is not None else position.mark_price
        if position.quantity != ZERO:
            if effective_mark is None:
                raise ValueError("an open position requires a mark price")
            _positive(effective_mark, "mark_price")
            unrealized = position.quantity * (effective_mark - position.average_price)
            notional = abs(position.quantity) * effective_mark
        else:
            if effective_mark is not None:
                _positive(effective_mark, "mark_price")
            unrealized = ZERO
            notional = ZERO
        cash = self._cash(account_id)
        equity = cash + unrealized
        margin = self.profile.margin
        initial = notional * margin.initial_margin_rate
        maintenance = notional * margin.maintenance_margin_rate
        liquidation_fee = notional * margin.liquidation_fee_rate
        available = equity - initial
        excess = equity - maintenance - liquidation_fee
        liquidatable = position.quantity != ZERO and excess <= ZERO
        return MarginAccountSnapshot(
            account_id=account_id,
            symbol=self.profile.execution.rules.symbol,
            currency=margin.settlement_currency,
            observed_at=observed_at,
            cash_balance=cash,
            position_quantity=position.quantity,
            average_price=position.average_price,
            mark_price=effective_mark,
            position_notional=notional,
            unrealized_pnl=unrealized,
            equity=equity,
            initial_margin_requirement=initial,
            maintenance_margin_requirement=maintenance,
            liquidation_fee_reserve=liquidation_fee,
            available_equity=available,
            margin_excess=excess,
            liquidatable=liquidatable,
            profile_key=self.profile.profile_key,
        )

    def mark_and_evaluate(
        self,
        *,
        event_id: str,
        account_id: str,
        occurred_at: datetime,
        mark_price: Decimal,
    ) -> MarginAccountSnapshot:
        self.engine.mark_price(
            event_id=event_id,
            occurred_at=occurred_at,
            account_id=account_id,
            symbol=self.profile.execution.rules.symbol,
            price=mark_price,
        )
        return self.evaluate(
            account_id=account_id,
            observed_at=occurred_at,
            mark_price=mark_price,
        )

    def apply_funding(
        self,
        *,
        event_id: str,
        account_id: str,
        occurred_at: datetime,
        funding_rate: Decimal,
        mark_price: Decimal,
    ) -> FundingEvidence:
        self.profile.margin.funding_schedule.require_due(occurred_at)
        _finite(funding_rate, "funding_rate")
        _positive(mark_price, "mark_price")
        position = self._open_position(account_id)
        if self._funding_already_applied(account_id, occurred_at):
            raise ValueError("funding was already applied for this account and schedule time")
        notional = abs(position.quantity) * mark_price
        amount = -(position.quantity * mark_price * funding_rate)
        self.engine.adjust_cash(
            event_id=event_id,
            occurred_at=occurred_at,
            account_id=account_id,
            currency=self.profile.margin.settlement_currency,
            amount=amount,
            kind=CashFlowKind.FUNDING,
            symbol=position.symbol,
            reason=(
                f"funding schedule {self.profile.margin.funding_schedule.schedule_id}; "
                f"rate={funding_rate}"
            ),
        )
        return FundingEvidence(
            event_id=event_id,
            schedule_id=self.profile.margin.funding_schedule.schedule_id,
            occurred_at=occurred_at,
            account_id=account_id,
            symbol=position.symbol,
            position_quantity=position.quantity,
            mark_price=mark_price,
            funding_rate=funding_rate,
            position_notional=notional,
            cash_amount=amount,
            model_version=self.profile.margin.funding_schedule.model_version,
        )

    def accrue_borrow_interest(
        self,
        *,
        event_id: str,
        account_id: str,
        occurred_at: datetime,
        principal: Decimal,
        elapsed: timedelta,
    ) -> FinancingChargeEvidence:
        rate = self.profile.margin.borrow_rate_per_year
        if rate is None:
            raise ValueError("borrow interest is not configured")
        return self._accrue_interest(
            event_id=event_id,
            account_id=account_id,
            occurred_at=occurred_at,
            principal=principal,
            elapsed=elapsed,
            annual_rate=rate,
            kind=FinancingChargeKind.BORROW_INTEREST,
        )

    def accrue_margin_interest(
        self,
        *,
        event_id: str,
        account_id: str,
        occurred_at: datetime,
        principal: Decimal,
        elapsed: timedelta,
    ) -> FinancingChargeEvidence:
        rate = self.profile.margin.margin_interest_rate_per_year
        if rate is None:
            raise ValueError("margin interest is not configured")
        return self._accrue_interest(
            event_id=event_id,
            account_id=account_id,
            occurred_at=occurred_at,
            principal=principal,
            elapsed=elapsed,
            annual_rate=rate,
            kind=FinancingChargeKind.MARGIN_INTEREST,
        )

    def liquidate(
        self,
        *,
        liquidation_id: str,
        account_id: str,
        occurred_at: datetime,
        trigger_mark_price: Decimal,
        execution_price: Decimal,
    ) -> LiquidationResult:
        _text(liquidation_id, "liquidation_id")
        _positive(trigger_mark_price, "trigger_mark_price")
        _positive(execution_price, "execution_price")
        position = self._open_position(account_id)
        before = self.evaluate(
            account_id=account_id,
            observed_at=occurred_at,
            mark_price=trigger_mark_price,
        )
        if not before.liquidatable:
            raise ValueError("account does not meet the configured liquidation condition")
        self.engine.mark_price(
            event_id=f"{liquidation_id}-mark",
            occurred_at=occurred_at,
            account_id=account_id,
            symbol=position.symbol,
            price=trigger_mark_price,
        )
        side = OrderSide.SELL if position.quantity > ZERO else OrderSide.BUY
        quantity = abs(position.quantity)
        fee = quantity * execution_price * self.profile.margin.liquidation_fee_rate
        order_id = f"{liquidation_id}-order"
        self.engine.submit_order(
            event_id=f"{liquidation_id}-submit",
            occurred_at=occurred_at,
            order_id=order_id,
            intent_id=f"{liquidation_id}-intent",
            account_id=account_id,
            venue=self.profile.execution.profile.venue,
            symbol=position.symbol,
            side=side,
            quantity=quantity,
            order_type=ExecutionOrderType.MARKET,
        )
        self.engine.accept_order(
            event_id=f"{liquidation_id}-accept",
            occurred_at=occurred_at,
            order_id=order_id,
        )
        self.engine.record_fill(
            event_id=f"{liquidation_id}-fill-event",
            fill_id=f"{liquidation_id}-fill",
            occurred_at=occurred_at,
            order_id=order_id,
            quantity=quantity,
            price=execution_price,
            settlement_currency=self.profile.margin.settlement_currency,
            settlement_mode=CashSettlementMode.DERIVATIVE_PNL_ONLY,
            fee_amount=fee,
        )
        after = self.evaluate(
            account_id=account_id,
            observed_at=occurred_at,
            mark_price=execution_price,
        )
        return LiquidationResult(
            liquidation_id=liquidation_id,
            occurred_at=occurred_at,
            side=side,
            quantity=quantity,
            trigger_mark_price=trigger_mark_price,
            execution_price=execution_price,
            liquidation_fee=fee,
            before=before,
            after=after,
            model_version=self.profile.margin.model_version,
        )

    def _accrue_interest(
        self,
        *,
        event_id: str,
        account_id: str,
        occurred_at: datetime,
        principal: Decimal,
        elapsed: timedelta,
        annual_rate: Decimal,
        kind: FinancingChargeKind,
    ) -> FinancingChargeEvidence:
        _positive(principal, "principal")
        _aware(occurred_at, "occurred_at")
        if elapsed <= timedelta(0):
            raise ValueError("interest elapsed time must be positive")
        elapsed_seconds = Decimal(str(elapsed.total_seconds()))
        amount = -(principal * annual_rate * elapsed_seconds / SECONDS_PER_YEAR)
        self.engine.adjust_cash(
            event_id=event_id,
            occurred_at=occurred_at,
            account_id=account_id,
            currency=self.profile.margin.settlement_currency,
            amount=amount,
            kind=CashFlowKind.INTEREST,
            symbol=self.profile.execution.rules.symbol,
            reason=f"{kind.value}; profile={self.profile.margin.profile_id}",
        )
        return FinancingChargeEvidence(
            event_id=event_id,
            kind=kind,
            occurred_at=occurred_at,
            account_id=account_id,
            currency=self.profile.margin.settlement_currency,
            principal=principal,
            annual_rate=annual_rate,
            elapsed=elapsed,
            cash_amount=amount,
            model_version=self.profile.margin.model_version,
        )

    def _position(self, account_id: str) -> PositionState:
        symbol = self.profile.execution.rules.symbol
        for position in self.engine.state.positions:
            if position.account_id == account_id and position.symbol == symbol:
                return position
        return PositionState(account_id=account_id, symbol=symbol)

    def _open_position(self, account_id: str) -> PositionState:
        position = self._position(account_id)
        if position.quantity == ZERO:
            raise ValueError("operation requires an open position")
        return position

    def _cash(self, account_id: str) -> Decimal:
        currency = self.profile.margin.settlement_currency
        for balance in self.engine.state.cash:
            if balance.account_id == account_id and balance.currency == currency:
                return balance.balance
        return ZERO

    def _funding_already_applied(self, account_id: str, occurred_at: datetime) -> bool:
        symbol = self.profile.execution.rules.symbol
        return any(
            event.account_id == account_id
            and event.occurred_at == occurred_at
            and event.symbol == symbol
            and event.cash_flow_kind is CashFlowKind.FUNDING
            for event in self.engine.events
        )


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


def _unit_rate(value: Decimal, name: str, *, positive: bool = False) -> None:
    _finite(value, name)
    lower_invalid = value <= ZERO if positive else value < ZERO
    if lower_invalid or value > ONE:
        boundary = "(0, 1]" if positive else "[0, 1]"
        raise ValueError(f"{name} must be in {boundary}")
