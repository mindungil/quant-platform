"""Deterministic venue matching on top of the event-sourced execution engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from .execution_engine import (
    CashFlowKind,
    CashSettlementMode,
    EventSourcedExecutionEngine,
)
from .execution_profiles import (
    ExecutionProfileSnapshot,
    OrderConstraintCode,
    check_order_constraints,
    floor_to_increment,
)
from .finance import ExecutionOrderType, LiquidityRole, OrderSide

ZERO = Decimal("0")
ONE = Decimal("1")


class VenueOrderStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class VenueSimulationConfig:
    order_latency: timedelta = timedelta(0)
    max_volume_participation: Decimal = Decimal("1")
    settlement_mode: CashSettlementMode = CashSettlementMode.DERIVATIVE_PNL_ONLY
    model_version: str = "volume-participation-v1"

    def __post_init__(self) -> None:
        if self.order_latency < timedelta(0):
            raise ValueError("order_latency must be non-negative")
        if (
            not self.max_volume_participation.is_finite()
            or not ZERO < self.max_volume_participation <= ONE
        ):
            raise ValueError("max_volume_participation must be in (0, 1]")
        _text(self.model_version, "model_version")


@dataclass(frozen=True, slots=True)
class VenueQuote:
    quote_id: str
    observed_at: datetime
    symbol: str
    bid_price: Decimal
    ask_price: Decimal
    bid_quantity: Decimal
    ask_quantity: Decimal
    trade_price: Decimal
    trade_volume: Decimal

    def __post_init__(self) -> None:
        _text(self.quote_id, "quote_id")
        _aware(self.observed_at, "observed_at")
        _text(self.symbol, "symbol")
        for name in ("bid_price", "ask_price", "trade_price"):
            _positive(getattr(self, name), name)
        for name in ("bid_quantity", "ask_quantity", "trade_volume"):
            _nonnegative(getattr(self, name), name)
        if self.bid_price > self.ask_price:
            raise ValueError("bid_price must not exceed ask_price")


@dataclass(frozen=True, slots=True)
class VenueOrderRequest:
    order_id: str
    intent_id: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: ExecutionOrderType
    submitted_at: datetime
    limit_price: Decimal | None = None
    replaces_order_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("order_id", "intent_id", "account_id", "symbol"):
            _text(getattr(self, name), name)
        _positive(self.quantity, "quantity")
        _aware(self.submitted_at, "submitted_at")
        if self.order_type is ExecutionOrderType.LIMIT:
            if self.limit_price is None:
                raise ValueError("LIMIT orders require limit_price")
            _positive(self.limit_price, "limit_price")
        elif self.limit_price is not None:
            raise ValueError("MARKET orders must not define limit_price")


@dataclass(frozen=True, slots=True)
class VenueOrderRecord:
    request: VenueOrderRequest
    profile_snapshot_id: str
    status: VenueOrderStatus
    accepted_at: datetime | None
    violations: tuple[OrderConstraintCode, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class VenueFillEvidence:
    fill_id: str
    order_id: str
    profile_snapshot_id: str
    model_version: str
    quote_id: str
    executed_at: datetime
    quote_observed_at: datetime
    quantity: Decimal
    price: Decimal
    liquidity_role: LiquidityRole
    available_quantity: Decimal
    participation_limit: Decimal
    fee_amount: Decimal
    signed_fee_cashflow: Decimal


@dataclass(frozen=True, slots=True)
class VenueMatchResult:
    order: VenueOrderRecord
    fill: VenueFillEvidence | None
    remaining_quantity: Decimal


class DeterministicVenueSimulator:
    def __init__(
        self,
        snapshot: ExecutionProfileSnapshot,
        config: VenueSimulationConfig | None = None,
        engine: EventSourcedExecutionEngine | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.config = config or VenueSimulationConfig()
        self.engine = engine or EventSourcedExecutionEngine()
        self._orders: dict[str, VenueOrderRecord] = {}
        self._fill_count = 0

    @property
    def orders(self) -> tuple[VenueOrderRecord, ...]:
        return tuple(self._orders.values())

    def submit(
        self, request: VenueOrderRequest, *, reference_quote: VenueQuote
    ) -> VenueOrderRecord:
        if request.order_id in self._orders:
            raise ValueError(f"order already exists: {request.order_id}")
        self._check_context(request, reference_quote)
        reference_price = (
            reference_quote.ask_price
            if request.side is OrderSide.BUY
            else reference_quote.bid_price
        )
        constraints = check_order_constraints(
            self.snapshot.rules,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.limit_price,
            reference_price=reference_price,
        )
        self.engine.submit_order(
            event_id=f"venue-{request.order_id}-submit",
            occurred_at=request.submitted_at,
            order_id=request.order_id,
            intent_id=request.intent_id,
            account_id=request.account_id,
            venue=self.snapshot.profile.venue,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            order_type=request.order_type,
            limit_price=request.limit_price,
        )
        if constraints.violations:
            reason = ", ".join(violation.value for violation in constraints.violations)
            self.engine.reject_order(
                event_id=f"venue-{request.order_id}-reject",
                occurred_at=request.submitted_at,
                order_id=request.order_id,
                reason=reason,
            )
            record = VenueOrderRecord(
                request=request,
                profile_snapshot_id=self.snapshot.snapshot_id,
                status=VenueOrderStatus.REJECTED,
                accepted_at=None,
                violations=constraints.violations,
                reason=reason,
            )
        else:
            accepted_at = request.submitted_at + self.config.order_latency
            self.engine.accept_order(
                event_id=f"venue-{request.order_id}-accept",
                occurred_at=accepted_at,
                order_id=request.order_id,
            )
            record = VenueOrderRecord(
                request=request,
                profile_snapshot_id=self.snapshot.snapshot_id,
                status=VenueOrderStatus.ACCEPTED,
                accepted_at=accepted_at,
            )
        self._orders[request.order_id] = record
        return record

    def match(self, order_id: str, quote: VenueQuote) -> VenueMatchResult:
        record = self._require_active(order_id)
        self._check_context(record.request, quote)
        assert record.accepted_at is not None
        if quote.observed_at < record.accepted_at:
            raise ValueError("quote precedes order arrival")
        role, price, available = self._match_terms(record.request, quote)
        if role is None:
            return VenueMatchResult(record, None, self._remaining(order_id))
        remaining = self._remaining(order_id)
        participation_limit = available * self.config.max_volume_participation
        step = self._quantity_step(record.request.order_type)
        fill_quantity = min(remaining, participation_limit)
        if step is not None:
            fill_quantity = floor_to_increment(fill_quantity, step)
        if fill_quantity <= ZERO:
            return VenueMatchResult(record, None, remaining)
        rate = (
            self.snapshot.profile.maker_fee_rate
            if role is LiquidityRole.MAKER
            else self.snapshot.profile.taker_fee_rate
        )
        notional = fill_quantity * price * self.snapshot.rules.contract_multiplier
        signed_fee = -notional * rate
        charged = max(ZERO, -signed_fee)
        self._fill_count += 1
        fill_id = f"venue-fill-{self._fill_count}"
        self.engine.record_fill(
            event_id=f"{fill_id}-event",
            fill_id=fill_id,
            occurred_at=quote.observed_at,
            order_id=order_id,
            quantity=fill_quantity,
            price=price,
            settlement_currency=self.snapshot.profile.settlement_currency,
            settlement_mode=self.config.settlement_mode,
            fee_amount=charged,
        )
        if signed_fee > ZERO:
            self.engine.adjust_cash(
                event_id=f"{fill_id}-rebate",
                occurred_at=quote.observed_at,
                account_id=record.request.account_id,
                currency=self.snapshot.profile.settlement_currency,
                amount=signed_fee,
                kind=CashFlowKind.OTHER,
                symbol=record.request.symbol,
                reason="maker rebate",
            )
        remaining_after = self._remaining(order_id)
        status = (
            VenueOrderStatus.FILLED
            if remaining_after == ZERO
            else VenueOrderStatus.PARTIALLY_FILLED
        )
        updated = VenueOrderRecord(
            request=record.request,
            profile_snapshot_id=record.profile_snapshot_id,
            status=status,
            accepted_at=record.accepted_at,
        )
        self._orders[order_id] = updated
        evidence = VenueFillEvidence(
            fill_id=fill_id,
            order_id=order_id,
            profile_snapshot_id=self.snapshot.snapshot_id,
            model_version=self.config.model_version,
            quote_id=quote.quote_id,
            executed_at=quote.observed_at,
            quote_observed_at=quote.observed_at,
            quantity=fill_quantity,
            price=price,
            liquidity_role=role,
            available_quantity=available,
            participation_limit=participation_limit,
            fee_amount=charged,
            signed_fee_cashflow=signed_fee,
        )
        return VenueMatchResult(updated, evidence, remaining_after)

    def cancel(
        self, order_id: str, *, occurred_at: datetime, reason: str = ""
    ) -> VenueOrderRecord:
        record = self._require_active(order_id)
        self.engine.cancel_order(
            event_id=f"venue-{order_id}-cancel",
            occurred_at=occurred_at,
            order_id=order_id,
            reason=reason,
        )
        updated = VenueOrderRecord(
            request=record.request,
            profile_snapshot_id=record.profile_snapshot_id,
            status=VenueOrderStatus.CANCELLED,
            accepted_at=record.accepted_at,
            reason=reason,
        )
        self._orders[order_id] = updated
        return updated

    def replace(
        self,
        order_id: str,
        replacement: VenueOrderRequest,
        *,
        occurred_at: datetime,
        reference_quote: VenueQuote,
    ) -> VenueOrderRecord:
        if replacement.replaces_order_id != order_id:
            raise ValueError("replacement must reference the cancelled order")
        self.cancel(
            order_id,
            occurred_at=occurred_at,
            reason=f"replaced by {replacement.order_id}",
        )
        if replacement.submitted_at < occurred_at:
            raise ValueError("replacement submitted_at must not precede cancellation")
        return self.submit(replacement, reference_quote=reference_quote)

    def _match_terms(
        self, request: VenueOrderRequest, quote: VenueQuote
    ) -> tuple[LiquidityRole | None, Decimal, Decimal]:
        if request.order_type is ExecutionOrderType.MARKET:
            return (
                (LiquidityRole.TAKER, quote.ask_price, quote.ask_quantity)
                if request.side is OrderSide.BUY
                else (LiquidityRole.TAKER, quote.bid_price, quote.bid_quantity)
            )
        assert request.limit_price is not None
        if request.side is OrderSide.BUY and request.limit_price >= quote.ask_price:
            return LiquidityRole.TAKER, quote.ask_price, quote.ask_quantity
        if request.side is OrderSide.SELL and request.limit_price <= quote.bid_price:
            return LiquidityRole.TAKER, quote.bid_price, quote.bid_quantity
        if request.side is OrderSide.BUY and quote.trade_price <= request.limit_price:
            return LiquidityRole.MAKER, request.limit_price, quote.trade_volume
        if request.side is OrderSide.SELL and quote.trade_price >= request.limit_price:
            return LiquidityRole.MAKER, request.limit_price, quote.trade_volume
        return None, ZERO, ZERO

    def _check_context(self, request: VenueOrderRequest, quote: VenueQuote) -> None:
        if (
            request.symbol != self.snapshot.rules.symbol
            or quote.symbol != request.symbol
        ):
            raise ValueError("order, quote, and venue profile symbols must match")
        if request.submitted_at < self.snapshot.effective_from or (
            self.snapshot.effective_to is not None
            and request.submitted_at >= self.snapshot.effective_to
        ):
            raise ValueError("venue profile is not effective at order submission")

    def _quantity_step(self, order_type: ExecutionOrderType) -> Decimal | None:
        rules = self.snapshot.rules
        return (
            rules.market_quantity_step
            if order_type is ExecutionOrderType.MARKET
            and rules.market_lot_size_overrides
            else rules.quantity_step
        )

    def _require_active(self, order_id: str) -> VenueOrderRecord:
        try:
            record = self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"unknown venue order: {order_id}") from exc
        if record.status not in {
            VenueOrderStatus.ACCEPTED,
            VenueOrderStatus.PARTIALLY_FILLED,
        }:
            raise ValueError("venue order is not active")
        return record

    def _remaining(self, order_id: str) -> Decimal:
        for order in self.engine.state.orders:
            if order.order_id == order_id:
                return order.remaining_quantity
        raise KeyError(order_id)


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _nonnegative(value: Decimal, name: str) -> None:
    if not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be non-negative")
