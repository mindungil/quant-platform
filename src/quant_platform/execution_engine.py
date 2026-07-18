"""Deterministic event-sourced execution state and accounting primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .finance import ExecutionOrderType, OrderSide

ZERO = Decimal("0")
ONE = Decimal("1")


class EngineOrderState(StrEnum):
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ExecutionEventKind(StrEnum):
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    CASH_ADJUSTED = "CASH_ADJUSTED"
    MARK_PRICE = "MARK_PRICE"


class CashFlowKind(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FUNDING = "FUNDING"
    COMMISSION = "COMMISSION"
    INTEREST = "INTEREST"
    TAX = "TAX"
    OTHER = "OTHER"


class CashSettlementMode(StrEnum):
    SPOT_NOTIONAL = "SPOT_NOTIONAL"
    DERIVATIVE_PNL_ONLY = "DERIVATIVE_PNL_ONLY"


@dataclass(frozen=True, slots=True)
class EventOrder:
    order_id: str
    intent_id: str
    account_id: str
    venue: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: ExecutionOrderType
    submitted_at: datetime
    state: EngineOrderState
    filled_quantity: Decimal = ZERO
    accepted_at: datetime | None = None
    limit_price: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        for name in ("order_id", "intent_id", "account_id", "venue", "symbol"):
            _require_text(getattr(self, name), name)
        _require_positive(self.quantity, "quantity")
        _require_aware(self.submitted_at, "submitted_at")
        _require_non_negative(self.filled_quantity, "filled_quantity")
        if self.filled_quantity > self.quantity:
            raise ValueError("filled_quantity must not exceed quantity")
        if self.accepted_at is not None:
            _require_aware(self.accepted_at, "accepted_at")
            if self.accepted_at < self.submitted_at:
                raise ValueError("accepted_at must not precede submitted_at")
        if self.order_type is ExecutionOrderType.LIMIT:
            if self.limit_price is None:
                raise ValueError("LIMIT orders require limit_price")
            _require_positive(self.limit_price, "limit_price")
        elif self.limit_price is not None:
            raise ValueError("MARKET orders must not define limit_price")

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class FillRecord:
    fill_id: str
    order_id: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    executed_at: datetime
    settlement_currency: str
    settlement_mode: CashSettlementMode
    fee_amount: Decimal = ZERO

    def __post_init__(self) -> None:
        for name in ("fill_id", "order_id", "account_id", "symbol", "settlement_currency"):
            _require_text(getattr(self, name), name)
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")
        _require_aware(self.executed_at, "executed_at")
        _require_non_negative(self.fee_amount, "fee_amount")


@dataclass(frozen=True, slots=True)
class PositionState:
    account_id: str
    symbol: str
    quantity: Decimal = ZERO
    average_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal = ZERO

    def __post_init__(self) -> None:
        _require_text(self.account_id, "account_id")
        _require_text(self.symbol, "symbol")
        _require_finite(self.quantity, "quantity")
        _require_non_negative(self.average_price, "average_price")
        _require_finite(self.realized_pnl, "realized_pnl")
        _require_finite(self.unrealized_pnl, "unrealized_pnl")
        if self.quantity == ZERO and self.average_price != ZERO:
            raise ValueError("flat positions must have zero average_price")
        if self.quantity != ZERO and self.average_price <= ZERO:
            raise ValueError("open positions require positive average_price")
        if self.mark_price is not None:
            _require_positive(self.mark_price, "mark_price")


@dataclass(frozen=True, slots=True)
class CashBalance:
    account_id: str
    currency: str
    balance: Decimal = ZERO

    def __post_init__(self) -> None:
        _require_text(self.account_id, "account_id")
        _require_text(self.currency, "currency")
        _require_finite(self.balance, "balance")


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    sequence: int
    event_id: str
    occurred_at: datetime
    kind: ExecutionEventKind
    account_id: str
    order_id: str | None = None
    intent_id: str | None = None
    fill_id: str | None = None
    venue: str | None = None
    symbol: str | None = None
    side: OrderSide | None = None
    order_type: ExecutionOrderType | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    limit_price: Decimal | None = None
    settlement_currency: str | None = None
    settlement_mode: CashSettlementMode | None = None
    fee_amount: Decimal | None = None
    cash_flow_kind: CashFlowKind | None = None
    cash_amount: Decimal | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        _require_text(self.event_id, "event_id")
        _require_text(self.account_id, "account_id")
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class ExecutionState:
    orders: tuple[EventOrder, ...] = ()
    fills: tuple[FillRecord, ...] = ()
    positions: tuple[PositionState, ...] = ()
    cash: tuple[CashBalance, ...] = ()

    def to_json(self) -> str:
        payload = {
            "orders": [
                _order_dict(order)
                for order in sorted(self.orders, key=lambda item: item.order_id)
            ],
            "fills": [
                _fill_dict(fill)
                for fill in sorted(self.fills, key=lambda item: item.fill_id)
            ],
            "positions": [
                _position_dict(position)
                for position in sorted(
                    self.positions,
                    key=lambda item: (item.account_id, item.symbol),
                )
            ],
            "cash": [
                _cash_dict(balance)
                for balance in sorted(
                    self.cash,
                    key=lambda item: (item.account_id, item.currency),
                )
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class EventSourcedExecutionEngine:
    """Append-only execution engine with deterministic replay."""

    def __init__(self, events: tuple[ExecutionEvent, ...] = ()) -> None:
        self._events: list[ExecutionEvent] = []
        self._orders: dict[str, EventOrder] = {}
        self._fills: dict[str, FillRecord] = {}
        self._positions: dict[tuple[str, str], PositionState] = {}
        self._cash: dict[tuple[str, str], CashBalance] = {}
        self._event_ids: set[str] = set()
        for event in events:
            self._append_existing(event)

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        return tuple(self._events)

    @property
    def state(self) -> ExecutionState:
        return ExecutionState(
            orders=tuple(self._orders.values()),
            fills=tuple(self._fills.values()),
            positions=tuple(self._positions.values()),
            cash=tuple(self._cash.values()),
        )

    @classmethod
    def replay(cls, events: tuple[ExecutionEvent, ...]) -> EventSourcedExecutionEngine:
        return cls(events)

    def events_json(self) -> str:
        return json.dumps(
            [_event_dict(event) for event in self._events],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def submit_order(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        order_id: str,
        intent_id: str,
        account_id: str,
        venue: str,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        order_type: ExecutionOrderType,
        limit_price: Decimal | None = None,
    ) -> EventOrder:
        if order_id in self._orders:
            raise ValueError(f"order already exists: {order_id}")
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=ExecutionEventKind.ORDER_SUBMITTED,
            account_id=account_id,
            order_id=order_id,
            intent_id=intent_id,
            venue=venue,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
        )
        self._append(event)
        return self._orders[order_id]

    def accept_order(self, *, event_id: str, occurred_at: datetime, order_id: str) -> EventOrder:
        order = self._require_order(order_id)
        if order.state is not EngineOrderState.SUBMITTED:
            raise ValueError("only submitted orders can be accepted")
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=ExecutionEventKind.ORDER_ACCEPTED,
            account_id=order.account_id,
            order_id=order_id,
        )
        self._append(event)
        return self._orders[order_id]

    def reject_order(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        order_id: str,
        reason: str,
    ) -> EventOrder:
        order = self._require_order(order_id)
        if order.state is not EngineOrderState.SUBMITTED:
            raise ValueError("only submitted orders can be rejected")
        _require_text(reason, "reason")
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=ExecutionEventKind.ORDER_REJECTED,
            account_id=order.account_id,
            order_id=order_id,
            reason=reason,
        )
        self._append(event)
        return self._orders[order_id]

    def cancel_order(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        order_id: str,
        reason: str = "",
    ) -> EventOrder:
        order = self._require_order(order_id)
        if order.state not in {
            EngineOrderState.SUBMITTED,
            EngineOrderState.ACCEPTED,
            EngineOrderState.PARTIALLY_FILLED,
        }:
            raise ValueError("only active orders can be cancelled")
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=ExecutionEventKind.ORDER_CANCELLED,
            account_id=order.account_id,
            order_id=order_id,
            reason=reason,
        )
        self._append(event)
        return self._orders[order_id]

    def record_fill(
        self,
        *,
        event_id: str,
        fill_id: str,
        occurred_at: datetime,
        order_id: str,
        quantity: Decimal,
        price: Decimal,
        settlement_currency: str,
        settlement_mode: CashSettlementMode,
        fee_amount: Decimal = ZERO,
    ) -> FillRecord:
        order = self._require_order(order_id)
        if order.state not in {EngineOrderState.ACCEPTED, EngineOrderState.PARTIALLY_FILLED}:
            raise ValueError("fills require an accepted active order")
        if fill_id in self._fills:
            raise ValueError(f"fill already exists: {fill_id}")
        _require_positive(quantity, "quantity")
        _require_positive(price, "price")
        _require_non_negative(fee_amount, "fee_amount")
        _require_text(settlement_currency, "settlement_currency")
        if quantity > order.remaining_quantity:
            raise ValueError("fill quantity exceeds remaining order quantity")
        resulting = order.filled_quantity + quantity
        kind = (
            ExecutionEventKind.ORDER_FILLED
            if resulting == order.quantity
            else ExecutionEventKind.ORDER_PARTIALLY_FILLED
        )
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=kind,
            account_id=order.account_id,
            order_id=order_id,
            fill_id=fill_id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            price=price,
            settlement_currency=settlement_currency,
            settlement_mode=settlement_mode,
            fee_amount=fee_amount,
        )
        self._append(event)
        return self._fills[fill_id]

    def adjust_cash(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        account_id: str,
        currency: str,
        amount: Decimal,
        kind: CashFlowKind,
        symbol: str | None = None,
        reason: str = "",
    ) -> CashBalance:
        _require_text(currency, "currency")
        _require_finite(amount, "amount")
        if kind is CashFlowKind.DEPOSIT and amount <= ZERO:
            raise ValueError("deposits require a positive amount")
        if kind in {
            CashFlowKind.WITHDRAWAL,
            CashFlowKind.COMMISSION,
            CashFlowKind.INTEREST,
            CashFlowKind.TAX,
        } and amount >= ZERO:
            raise ValueError(f"{kind.value} requires a negative amount")
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=ExecutionEventKind.CASH_ADJUSTED,
            account_id=account_id,
            symbol=symbol,
            settlement_currency=currency,
            cash_flow_kind=kind,
            cash_amount=amount,
            reason=reason,
        )
        self._append(event)
        return self._cash[(account_id, currency)]

    def mark_price(
        self,
        *,
        event_id: str,
        occurred_at: datetime,
        account_id: str,
        symbol: str,
        price: Decimal,
    ) -> PositionState:
        position = self._positions.get((account_id, symbol))
        if position is None or position.quantity == ZERO:
            raise ValueError("mark_price requires an open position")
        _require_positive(price, "price")
        event = self._new_event(
            event_id=event_id,
            occurred_at=occurred_at,
            kind=ExecutionEventKind.MARK_PRICE,
            account_id=account_id,
            symbol=symbol,
            price=price,
        )
        self._append(event)
        return self._positions[(account_id, symbol)]

    def _new_event(self, **values: object) -> ExecutionEvent:
        return ExecutionEvent(sequence=len(self._events) + 1, **values)  # type: ignore[arg-type]

    def _append_existing(self, event: ExecutionEvent) -> None:
        expected = len(self._events) + 1
        if event.sequence != expected:
            raise ValueError(f"event sequence must be contiguous; expected {expected}")
        self._append(event)

    def _append(self, event: ExecutionEvent) -> None:
        if event.event_id in self._event_ids:
            raise ValueError(f"event already exists: {event.event_id}")
        if self._events and event.occurred_at < self._events[-1].occurred_at:
            raise ValueError("event timestamps must be non-decreasing")
        self._apply(event)
        self._events.append(event)
        self._event_ids.add(event.event_id)

    def _apply(self, event: ExecutionEvent) -> None:
        if event.kind is ExecutionEventKind.ORDER_SUBMITTED:
            self._apply_submit(event)
        elif event.kind is ExecutionEventKind.ORDER_ACCEPTED:
            self._apply_accept(event)
        elif event.kind is ExecutionEventKind.ORDER_REJECTED:
            self._apply_terminal(event, EngineOrderState.REJECTED)
        elif event.kind is ExecutionEventKind.ORDER_CANCELLED:
            self._apply_terminal(event, EngineOrderState.CANCELLED)
        elif event.kind in {
            ExecutionEventKind.ORDER_PARTIALLY_FILLED,
            ExecutionEventKind.ORDER_FILLED,
        }:
            self._apply_fill(event)
        elif event.kind is ExecutionEventKind.CASH_ADJUSTED:
            self._apply_cash(event)
        elif event.kind is ExecutionEventKind.MARK_PRICE:
            self._apply_mark(event)
        else:
            raise ValueError(f"unsupported event kind: {event.kind}")

    def _apply_submit(self, event: ExecutionEvent) -> None:
        if event.order_id is None or event.intent_id is None or event.venue is None:
            raise ValueError("submitted order event is incomplete")
        if event.symbol is None or event.side is None or event.quantity is None:
            raise ValueError("submitted order event is incomplete")
        if event.order_type is None:
            raise ValueError("submitted order event is incomplete")
        if event.order_id in self._orders:
            raise ValueError(f"order already exists: {event.order_id}")
        self._orders[event.order_id] = EventOrder(
            order_id=event.order_id,
            intent_id=event.intent_id,
            account_id=event.account_id,
            venue=event.venue,
            symbol=event.symbol,
            side=event.side,
            quantity=event.quantity,
            order_type=event.order_type,
            submitted_at=event.occurred_at,
            state=EngineOrderState.SUBMITTED,
            limit_price=event.limit_price,
        )

    def _apply_accept(self, event: ExecutionEvent) -> None:
        order = self._event_order(event)
        if order.state is not EngineOrderState.SUBMITTED:
            raise ValueError("only submitted orders can be accepted")
        self._orders[order.order_id] = replace(
            order,
            state=EngineOrderState.ACCEPTED,
            accepted_at=event.occurred_at,
        )

    def _apply_terminal(self, event: ExecutionEvent, state: EngineOrderState) -> None:
        order = self._event_order(event)
        if state is EngineOrderState.REJECTED:
            allowed = {EngineOrderState.SUBMITTED}
        else:
            allowed = {
                EngineOrderState.SUBMITTED,
                EngineOrderState.ACCEPTED,
                EngineOrderState.PARTIALLY_FILLED,
            }
        if order.state not in allowed:
            raise ValueError("terminal event is invalid for current order state")
        self._orders[order.order_id] = replace(order, state=state, reason=event.reason)

    def _apply_fill(self, event: ExecutionEvent) -> None:
        order = self._event_order(event)
        if order.state not in {EngineOrderState.ACCEPTED, EngineOrderState.PARTIALLY_FILLED}:
            raise ValueError("fills require an accepted active order")
        if event.fill_id is None or event.fill_id in self._fills:
            raise ValueError("fill event requires a unique fill_id")
        if event.quantity is None or event.price is None:
            raise ValueError("fill event requires quantity and price")
        if event.settlement_currency is None or event.settlement_mode is None:
            raise ValueError("fill event requires settlement details")
        fee = event.fee_amount if event.fee_amount is not None else ZERO
        if event.quantity > order.remaining_quantity:
            raise ValueError("fill quantity exceeds remaining order quantity")
        new_filled = order.filled_quantity + event.quantity
        expected_kind = (
            ExecutionEventKind.ORDER_FILLED
            if new_filled == order.quantity
            else ExecutionEventKind.ORDER_PARTIALLY_FILLED
        )
        if event.kind is not expected_kind:
            raise ValueError("fill event kind does not match cumulative quantity")
        new_state = (
            EngineOrderState.FILLED
            if new_filled == order.quantity
            else EngineOrderState.PARTIALLY_FILLED
        )
        self._orders[order.order_id] = replace(
            order,
            state=new_state,
            filled_quantity=new_filled,
        )
        fill = FillRecord(
            fill_id=event.fill_id,
            order_id=order.order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            side=order.side,
            quantity=event.quantity,
            price=event.price,
            executed_at=event.occurred_at,
            settlement_currency=event.settlement_currency,
            settlement_mode=event.settlement_mode,
            fee_amount=fee,
        )
        self._fills[fill.fill_id] = fill
        realized_delta = self._update_position(fill)
        signed_quantity = event.quantity if order.side is OrderSide.BUY else -event.quantity
        if event.settlement_mode is CashSettlementMode.SPOT_NOTIONAL:
            cash_delta = -(signed_quantity * event.price) - fee
        else:
            cash_delta = realized_delta - fee
        self._add_cash(order.account_id, event.settlement_currency, cash_delta)

    def _update_position(self, fill: FillRecord) -> Decimal:
        key = (fill.account_id, fill.symbol)
        current = self._positions.get(key, PositionState(fill.account_id, fill.symbol))
        signed_fill = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
        old_quantity = current.quantity
        new_quantity = old_quantity + signed_fill
        realized_delta = ZERO
        average_price = current.average_price

        if old_quantity == ZERO or old_quantity * signed_fill > ZERO:
            total_cost = abs(old_quantity) * average_price + abs(signed_fill) * fill.price
            average_price = total_cost / abs(new_quantity)
        else:
            closing_quantity = min(abs(old_quantity), abs(signed_fill))
            direction = ONE if old_quantity > ZERO else -ONE
            realized_delta = closing_quantity * (fill.price - average_price) * direction
            if new_quantity == ZERO:
                average_price = ZERO
            elif old_quantity * new_quantity > ZERO:
                average_price = current.average_price
            else:
                average_price = fill.price

        mark_price = current.mark_price
        unrealized = (
            ZERO
            if mark_price is None or new_quantity == ZERO
            else new_quantity * (mark_price - average_price)
        )
        self._positions[key] = PositionState(
            account_id=fill.account_id,
            symbol=fill.symbol,
            quantity=new_quantity,
            average_price=average_price,
            realized_pnl=current.realized_pnl + realized_delta,
            mark_price=mark_price,
            unrealized_pnl=unrealized,
        )
        return realized_delta

    def _apply_cash(self, event: ExecutionEvent) -> None:
        if event.settlement_currency is None or event.cash_amount is None:
            raise ValueError("cash event requires currency and amount")
        if event.cash_flow_kind is None:
            raise ValueError("cash event requires cash_flow_kind")
        self._add_cash(event.account_id, event.settlement_currency, event.cash_amount)

    def _apply_mark(self, event: ExecutionEvent) -> None:
        if event.symbol is None or event.price is None:
            raise ValueError("mark event requires symbol and price")
        key = (event.account_id, event.symbol)
        position = self._positions.get(key)
        if position is None or position.quantity == ZERO:
            raise ValueError("mark event requires an open position")
        self._positions[key] = replace(
            position,
            mark_price=event.price,
            unrealized_pnl=position.quantity * (event.price - position.average_price),
        )

    def _add_cash(self, account_id: str, currency: str, amount: Decimal) -> None:
        key = (account_id, currency)
        current = self._cash.get(key, CashBalance(account_id, currency))
        self._cash[key] = replace(current, balance=current.balance + amount)

    def _event_order(self, event: ExecutionEvent) -> EventOrder:
        if event.order_id is None:
            raise ValueError("order event requires order_id")
        return self._require_order(event.order_id)

    def _require_order(self, order_id: str) -> EventOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise KeyError(f"unknown order: {order_id}") from exc


def _event_dict(event: ExecutionEvent) -> dict[str, object]:
    return {
        "sequence": event.sequence,
        "event_id": event.event_id,
        "occurred_at": _timestamp(event.occurred_at),
        "kind": event.kind.value,
        "account_id": event.account_id,
        "order_id": event.order_id,
        "intent_id": event.intent_id,
        "fill_id": event.fill_id,
        "venue": event.venue,
        "symbol": event.symbol,
        "side": event.side.value if event.side is not None else None,
        "order_type": event.order_type.value if event.order_type is not None else None,
        "quantity": _decimal(event.quantity),
        "price": _decimal(event.price),
        "limit_price": _decimal(event.limit_price),
        "settlement_currency": event.settlement_currency,
        "settlement_mode": (
            event.settlement_mode.value if event.settlement_mode is not None else None
        ),
        "fee_amount": _decimal(event.fee_amount),
        "cash_flow_kind": (
            event.cash_flow_kind.value if event.cash_flow_kind is not None else None
        ),
        "cash_amount": _decimal(event.cash_amount),
        "reason": event.reason,
    }


def _order_dict(order: EventOrder) -> dict[str, object]:
    return {
        "order_id": order.order_id,
        "intent_id": order.intent_id,
        "account_id": order.account_id,
        "venue": order.venue,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": str(order.quantity),
        "order_type": order.order_type.value,
        "submitted_at": _timestamp(order.submitted_at),
        "state": order.state.value,
        "filled_quantity": str(order.filled_quantity),
        "accepted_at": (
            _timestamp(order.accepted_at) if order.accepted_at is not None else None
        ),
        "limit_price": _decimal(order.limit_price),
        "reason": order.reason,
    }


def _fill_dict(fill: FillRecord) -> dict[str, object]:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "account_id": fill.account_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "executed_at": _timestamp(fill.executed_at),
        "settlement_currency": fill.settlement_currency,
        "settlement_mode": fill.settlement_mode.value,
        "fee_amount": str(fill.fee_amount),
    }


def _position_dict(position: PositionState) -> dict[str, object]:
    return {
        "account_id": position.account_id,
        "symbol": position.symbol,
        "quantity": str(position.quantity),
        "average_price": str(position.average_price),
        "realized_pnl": str(position.realized_pnl),
        "mark_price": _decimal(position.mark_price),
        "unrealized_pnl": str(position.unrealized_pnl),
    }


def _cash_dict(balance: CashBalance) -> dict[str, object]:
    return {
        "account_id": balance.account_id,
        "currency": balance.currency,
        "balance": str(balance.balance),
    }


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _require_positive(value: Decimal, name: str) -> None:
    _require_finite(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(value: Decimal, name: str) -> None:
    _require_finite(value, name)
    if value < ZERO:
        raise ValueError(f"{name} must be non-negative")
