"""Versioned execution-profile snapshots and order-constraint utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from string import hexdigits

from .finance import (
    ExecutionFill,
    ExecutionOrderType,
    ExecutionRealityProfile,
    FinancialLedgerEntry,
    LedgerEntryKind,
    LiquidityRole,
)

ZERO = Decimal("0")


class ExecutionProfileConfidence(StrEnum):
    CONFIRMED = "confirmed"
    ASSUMED = "assumed"
    REVIEW_REQUIRED = "review_required"


class OrderConstraintCode(StrEnum):
    QUANTITY_NOT_POSITIVE = "quantity_not_positive"
    QUANTITY_BELOW_MINIMUM = "quantity_below_minimum"
    QUANTITY_ABOVE_MAXIMUM = "quantity_above_maximum"
    QUANTITY_STEP_MISMATCH = "quantity_step_mismatch"
    PRICE_REQUIRED = "price_required"
    REFERENCE_PRICE_REQUIRED = "reference_price_required"
    PRICE_NOT_POSITIVE = "price_not_positive"
    PRICE_BELOW_MINIMUM = "price_below_minimum"
    PRICE_ABOVE_MAXIMUM = "price_above_maximum"
    PRICE_TICK_MISMATCH = "price_tick_mismatch"
    NOTIONAL_BELOW_MINIMUM = "notional_below_minimum"
    NOTIONAL_ABOVE_MAXIMUM = "notional_above_maximum"


@dataclass(frozen=True, slots=True)
class ExecutionSourceEvidence:
    """One immutable source used to build an execution profile."""

    source_id: str
    reference: str
    observed_at: datetime
    sha256: str
    content_type: str = "application/json"

    def __post_init__(self) -> None:
        for name in ("source_id", "reference", "content_type"):
            _require_text(getattr(self, name), name)
        _require_aware(self.observed_at, "observed_at")
        normalized = self.sha256.lower()
        if len(normalized) != 64 or any(character not in hexdigits for character in normalized):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        object.__setattr__(self, "sha256", normalized)


@dataclass(frozen=True, slots=True)
class InstrumentExecutionRules:
    """Symbol-level order rules captured from a venue at a point in time."""

    symbol: str
    base_asset: str
    quote_asset: str
    price_tick: Decimal | None
    quantity_step: Decimal | None
    minimum_price: Decimal = ZERO
    maximum_price: Decimal | None = None
    minimum_quantity: Decimal = ZERO
    maximum_quantity: Decimal | None = None
    market_lot_size_overrides: bool = False
    market_quantity_step: Decimal | None = None
    market_minimum_quantity: Decimal | None = None
    market_maximum_quantity: Decimal | None = None
    minimum_notional: Decimal = ZERO
    maximum_notional: Decimal | None = None
    min_notional_applies_to_market: bool = True
    max_notional_applies_to_market: bool = True
    contract_multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for name in ("symbol", "base_asset", "quote_asset"):
            _require_text(getattr(self, name), name)
        for name in (
            "minimum_price",
            "minimum_quantity",
            "minimum_notional",
        ):
            _require_non_negative(getattr(self, name), name)
        for name in (
            "price_tick",
            "quantity_step",
            "market_quantity_step",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_positive(value, name)
        for name in (
            "maximum_price",
            "maximum_quantity",
            "market_minimum_quantity",
            "market_maximum_quantity",
            "maximum_notional",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_non_negative(value, name)
        _require_positive(self.contract_multiplier, "contract_multiplier")
        _require_ordered(self.minimum_price, self.maximum_price, "price")
        _require_ordered(self.minimum_quantity, self.maximum_quantity, "quantity")
        if self.market_minimum_quantity is not None:
            _require_ordered(
                self.market_minimum_quantity,
                self.market_maximum_quantity,
                "market quantity",
            )
        _require_ordered(self.minimum_notional, self.maximum_notional, "notional")


@dataclass(frozen=True, slots=True)
class ExecutionProfileSnapshot:
    """A reproducible execution profile plus the sources that produced it."""

    snapshot_id: str
    schema_version: str
    profile: ExecutionRealityProfile
    rules: InstrumentExecutionRules
    observed_at: datetime
    effective_from: datetime
    effective_to: datetime | None
    sources: tuple[ExecutionSourceEvidence, ...]
    confidence: ExecutionProfileConfidence

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "schema_version"):
            _require_text(getattr(self, name), name)
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.effective_from, "effective_from")
        if self.effective_to is not None:
            _require_aware(self.effective_to, "effective_to")
            if self.effective_to < self.effective_from:
                raise ValueError("effective_to must not precede effective_from")
        if not self.sources:
            raise ValueError("sources must not be empty")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        if self.profile.price_tick is not None and self.profile.price_tick != self.rules.price_tick:
            raise ValueError("profile price_tick must match symbol rules")
        if (
            self.profile.quantity_step is not None
            and self.profile.quantity_step != self.rules.quantity_step
        ):
            raise ValueError("profile quantity_step must match symbol rules")
        if self.profile.minimum_notional != self.rules.minimum_notional:
            raise ValueError("profile minimum_notional must match symbol rules")
        if self.profile.contract_multiplier != self.rules.contract_multiplier:
            raise ValueError("profile contract_multiplier must match symbol rules")


@dataclass(frozen=True, slots=True)
class OrderConstraintResult:
    violations: tuple[OrderConstraintCode, ...]
    notional: Decimal | None

    @property
    def accepted(self) -> bool:
        return not self.violations


def floor_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Floor a non-negative value to a positive venue increment."""

    _require_non_negative(value, "value")
    _require_positive(increment, "increment")
    return (value // increment) * increment


def check_order_constraints(
    rules: InstrumentExecutionRules,
    *,
    order_type: ExecutionOrderType,
    quantity: Decimal,
    price: Decimal | None = None,
    reference_price: Decimal | None = None,
) -> OrderConstraintResult:
    """Check deterministic symbol rules without silently rounding an order."""

    violations: list[OrderConstraintCode] = []
    if not quantity.is_finite() or quantity <= ZERO:
        return OrderConstraintResult(
            violations=(OrderConstraintCode.QUANTITY_NOT_POSITIVE,),
            notional=None,
        )

    is_market = order_type is ExecutionOrderType.MARKET
    if is_market and rules.market_lot_size_overrides:
        minimum_quantity = rules.market_minimum_quantity or ZERO
        maximum_quantity = rules.market_maximum_quantity
        quantity_step = rules.market_quantity_step
    else:
        minimum_quantity = rules.minimum_quantity
        maximum_quantity = rules.maximum_quantity
        quantity_step = rules.quantity_step

    if quantity < minimum_quantity:
        violations.append(OrderConstraintCode.QUANTITY_BELOW_MINIMUM)
    if maximum_quantity is not None and quantity > maximum_quantity:
        violations.append(OrderConstraintCode.QUANTITY_ABOVE_MAXIMUM)
    if quantity_step is not None and quantity % quantity_step != ZERO:
        violations.append(OrderConstraintCode.QUANTITY_STEP_MISMATCH)

    effective_price: Decimal | None
    if is_market:
        effective_price = reference_price
        requires_reference = (
            rules.min_notional_applies_to_market and rules.minimum_notional > ZERO
        ) or (
            rules.max_notional_applies_to_market and rules.maximum_notional is not None
        )
        if requires_reference and effective_price is None:
            violations.append(OrderConstraintCode.REFERENCE_PRICE_REQUIRED)
    else:
        effective_price = price
        if effective_price is None:
            violations.append(OrderConstraintCode.PRICE_REQUIRED)

    if effective_price is not None:
        if not effective_price.is_finite() or effective_price <= ZERO:
            violations.append(OrderConstraintCode.PRICE_NOT_POSITIVE)
        elif not is_market:
            if effective_price < rules.minimum_price:
                violations.append(OrderConstraintCode.PRICE_BELOW_MINIMUM)
            if rules.maximum_price is not None and effective_price > rules.maximum_price:
                violations.append(OrderConstraintCode.PRICE_ABOVE_MAXIMUM)
            if rules.price_tick is not None and effective_price % rules.price_tick != ZERO:
                violations.append(OrderConstraintCode.PRICE_TICK_MISMATCH)

    notional = None
    if effective_price is not None and effective_price.is_finite() and effective_price > ZERO:
        notional = effective_price * quantity * rules.contract_multiplier
        minimum_applies = not is_market or rules.min_notional_applies_to_market
        maximum_applies = not is_market or rules.max_notional_applies_to_market
        if minimum_applies and notional < rules.minimum_notional:
            violations.append(OrderConstraintCode.NOTIONAL_BELOW_MINIMUM)
        if (
            maximum_applies
            and rules.maximum_notional is not None
            and notional > rules.maximum_notional
        ):
            violations.append(OrderConstraintCode.NOTIONAL_ABOVE_MAXIMUM)

    return OrderConstraintResult(violations=tuple(violations), notional=notional)


def require_order_constraints(
    rules: InstrumentExecutionRules,
    *,
    order_type: ExecutionOrderType,
    quantity: Decimal,
    price: Decimal | None = None,
    reference_price: Decimal | None = None,
) -> Decimal | None:
    """Raise when an order violates a frozen venue profile."""

    result = check_order_constraints(
        rules,
        order_type=order_type,
        quantity=quantity,
        price=price,
        reference_price=reference_price,
    )
    if result.violations:
        labels = ", ".join(violation.value for violation in result.violations)
        raise ValueError(f"order violates execution profile: {labels}")
    return result.notional


def fill_notional(fill: ExecutionFill, rules: InstrumentExecutionRules) -> Decimal:
    if fill.symbol != rules.symbol:
        raise ValueError("fill symbol must match symbol rules")
    return fill.quantity * fill.price * rules.contract_multiplier


def settlement_value_fee_entry(
    *,
    entry_id: str,
    fill: ExecutionFill,
    snapshot: ExecutionProfileSnapshot,
    description: str = "",
) -> FinancialLedgerEntry:
    """Record a quote-notional fee or rebate in the profile settlement currency.

    Use this helper only when the venue charges the fill fee from quote notional
    in the settlement currency. Received-asset and third-asset fees require a
    venue-specific adapter.
    """

    if fill.venue != snapshot.profile.venue:
        raise ValueError("fill venue must match execution profile")
    if fill.liquidity_role is LiquidityRole.UNKNOWN:
        raise ValueError("fee calculation requires a known liquidity role")
    rate = (
        snapshot.profile.maker_fee_rate
        if fill.liquidity_role is LiquidityRole.MAKER
        else snapshot.profile.taker_fee_rate
    )
    notional = fill_notional(fill, snapshot.rules)
    if rate < ZERO:
        kind = LedgerEntryKind.REBATE
        amount = notional * -rate
    else:
        kind = LedgerEntryKind.COMMISSION
        amount = -(notional * rate)
    return FinancialLedgerEntry(
        entry_id=entry_id,
        occurred_at=fill.executed_at,
        account_id=fill.account_id,
        currency=snapshot.profile.settlement_currency,
        kind=kind,
        amount=amount,
        symbol=fill.symbol,
        related_id=fill.fill_id,
        description=description or f"execution profile {snapshot.snapshot_id}",
    )


def funding_ledger_entry(
    *,
    entry_id: str,
    occurred_at: datetime,
    account_id: str,
    currency: str,
    symbol: str,
    amount: Decimal,
    related_id: str,
    description: str = "",
) -> FinancialLedgerEntry:
    """Record a signed funding cash flow without changing its account perspective."""

    return FinancialLedgerEntry(
        entry_id=entry_id,
        occurred_at=occurred_at,
        account_id=account_id,
        currency=currency,
        kind=LedgerEntryKind.FUNDING,
        amount=amount,
        symbol=symbol,
        related_id=related_id,
        description=description,
    )


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _require_non_negative(value: Decimal, name: str) -> None:
    _require_finite(value, name)
    if value < ZERO:
        raise ValueError(f"{name} must be non-negative")


def _require_positive(value: Decimal, name: str) -> None:
    _require_finite(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _require_ordered(
    minimum: Decimal,
    maximum: Decimal | None,
    label: str,
) -> None:
    if maximum is not None and maximum < minimum:
        raise ValueError(f"maximum {label} must not be below minimum {label}")
