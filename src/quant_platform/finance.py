"""Public financial, execution-reality, and tax-ledger contracts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

ZERO = Decimal("0")


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ExecutionOrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderState(StrEnum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    UNKNOWN = "UNKNOWN"


class LedgerEntryKind(StrEnum):
    """Signed account-value components.

    Positive amounts increase account value and negative amounts decrease it.
    Annual income-tax estimates are intentionally not ledger entries.
    """

    REALIZED_PNL = "REALIZED_PNL"
    COMMISSION = "COMMISSION"
    REBATE = "REBATE"
    SPREAD = "SPREAD"
    SLIPPAGE = "SLIPPAGE"
    MARKET_IMPACT = "MARKET_IMPACT"
    FUNDING = "FUNDING"
    BORROW_INTEREST = "BORROW_INTEREST"
    MARGIN_INTEREST = "MARGIN_INTEREST"
    TRANSACTION_TAX = "TRANSACTION_TAX"
    FX_COST = "FX_COST"
    CASH_MOVEMENT = "CASH_MOVEMENT"
    TAX_WITHHOLDING = "TAX_WITHHOLDING"
    TAX_PAYMENT = "TAX_PAYMENT"


class TaxConfidence(StrEnum):
    CONFIRMED = "confirmed"
    ASSUMED = "assumed"
    REVIEW_REQUIRED = "review_required"


class CostBasisMethod(StrEnum):
    FIFO = "FIFO"
    MOVING_AVERAGE = "MOVING_AVERAGE"
    SPECIFIC_IDENTIFICATION = "SPECIFIC_IDENTIFICATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSPECIFIED = "UNSPECIFIED"


class TaxableEventKind(StrEnum):
    DISPOSAL = "DISPOSAL"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"
    WITHHOLDING = "WITHHOLDING"
    OTHER = "OTHER"


_EXECUTION_KINDS = frozenset(
    {
        LedgerEntryKind.COMMISSION,
        LedgerEntryKind.REBATE,
        LedgerEntryKind.SPREAD,
        LedgerEntryKind.SLIPPAGE,
        LedgerEntryKind.MARKET_IMPACT,
        LedgerEntryKind.TRANSACTION_TAX,
        LedgerEntryKind.FX_COST,
    }
)
_FINANCING_KINDS = frozenset(
    {
        LedgerEntryKind.FUNDING,
        LedgerEntryKind.BORROW_INTEREST,
        LedgerEntryKind.MARGIN_INTEREST,
    }
)
_TAX_CASH_KINDS = frozenset(
    {
        LedgerEntryKind.TAX_WITHHOLDING,
        LedgerEntryKind.TAX_PAYMENT,
    }
)
_NON_POSITIVE_KINDS = frozenset(
    {
        LedgerEntryKind.COMMISSION,
        LedgerEntryKind.SPREAD,
        LedgerEntryKind.SLIPPAGE,
        LedgerEntryKind.MARKET_IMPACT,
        LedgerEntryKind.BORROW_INTEREST,
        LedgerEntryKind.MARGIN_INTEREST,
        LedgerEntryKind.TRANSACTION_TAX,
        LedgerEntryKind.FX_COST,
        LedgerEntryKind.TAX_WITHHOLDING,
        LedgerEntryKind.TAX_PAYMENT,
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionRealityProfile:
    """Venue and account assumptions used by execution and simulation adapters.

    Fee rates are decimal fractions: ``Decimal("0.001")`` means 10 basis points.
    Model fields are stable identifiers; the concrete implementations live
    outside this data contract.
    """

    profile_id: str
    venue: str
    market: str
    account_type: str
    settlement_currency: str
    maker_fee_rate: Decimal = ZERO
    taker_fee_rate: Decimal = ZERO
    minimum_notional: Decimal = ZERO
    quantity_step: Decimal | None = None
    price_tick: Decimal | None = None
    contract_multiplier: Decimal = Decimal("1")
    slippage_model: str = "none"
    spread_model: str = "none"
    market_impact_model: str = "none"
    funding_model: str = "none"
    borrow_model: str = "none"
    margin_model: str = "none"

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "venue",
            "market",
            "account_type",
            "settlement_currency",
            "slippage_model",
            "spread_model",
            "market_impact_model",
            "funding_model",
            "borrow_model",
            "margin_model",
        ):
            _require_text(getattr(self, name), name)

        for name in ("maker_fee_rate", "taker_fee_rate"):
            value = getattr(self, name)
            _require_finite(value, name)
            if value <= Decimal("-1"):
                raise ValueError(f"{name} must be greater than -1")

        _require_non_negative(self.minimum_notional, "minimum_notional")
        _require_positive(self.contract_multiplier, "contract_multiplier")
        if self.quantity_step is not None:
            _require_positive(self.quantity_step, "quantity_step")
        if self.price_tick is not None:
            _require_positive(self.price_tick, "price_tick")


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    intent_id: str
    strategy_id: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: ExecutionOrderType
    created_at: datetime
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("intent_id", "strategy_id", "account_id", "symbol"):
            _require_text(getattr(self, name), name)
        _require_positive(self.quantity, "quantity")
        _require_aware(self.created_at, "created_at")
        _validate_order_price(self.order_type, self.limit_price)


@dataclass(frozen=True, slots=True)
class ExecutionOrder:
    order_id: str
    intent_id: str
    venue: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: ExecutionOrderType
    submitted_at: datetime
    state: OrderState
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("order_id", "intent_id", "venue", "account_id", "symbol"):
            _require_text(getattr(self, name), name)
        _require_positive(self.quantity, "quantity")
        _require_aware(self.submitted_at, "submitted_at")
        _validate_order_price(self.order_type, self.limit_price)


@dataclass(frozen=True, slots=True)
class ExecutionFill:
    fill_id: str
    order_id: str
    venue: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    executed_at: datetime
    liquidity_role: LiquidityRole = LiquidityRole.UNKNOWN

    def __post_init__(self) -> None:
        for name in ("fill_id", "order_id", "venue", "account_id", "symbol"):
            _require_text(getattr(self, name), name)
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")
        _require_aware(self.executed_at, "executed_at")


@dataclass(frozen=True, slots=True)
class FinancialLedgerEntry:
    """One immutable signed financial event in a single currency."""

    entry_id: str
    occurred_at: datetime
    account_id: str
    currency: str
    kind: LedgerEntryKind
    amount: Decimal
    symbol: str | None = None
    related_id: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        for name in ("entry_id", "account_id", "currency"):
            _require_text(getattr(self, name), name)
        _require_aware(self.occurred_at, "occurred_at")
        _require_finite(self.amount, "amount")
        if self.symbol is not None:
            _require_text(self.symbol, "symbol")
        if self.related_id is not None:
            _require_text(self.related_id, "related_id")
        if self.kind in _NON_POSITIVE_KINDS and self.amount > ZERO:
            raise ValueError(f"{self.kind.value} amount must be non-positive")
        if self.kind is LedgerEntryKind.REBATE and self.amount < ZERO:
            raise ValueError("REBATE amount must be non-negative")


@dataclass(frozen=True, slots=True)
class FinancialLedgerSummary:
    account_id: str | None
    currency: str
    gross_realized_pnl: Decimal
    execution_adjustment: Decimal
    financing_adjustment: Decimal
    economic_net_pnl: Decimal
    tax_cash_flow: Decimal
    external_cash_movement: Decimal
    reconciled_value_change: Decimal


@dataclass(frozen=True, slots=True)
class FinancialLedger:
    """Chronological immutable ledger.

    The ledger may contain multiple accounts and currencies. Summary operations
    require an explicit reporting currency and never perform implicit FX
    conversion.
    """

    entries: tuple[FinancialLedgerEntry, ...] = ()

    def __post_init__(self) -> None:
        identifiers = [entry.entry_id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("financial ledger entry IDs must be unique")
        timestamps = [entry.occurred_at for entry in self.entries]
        if timestamps != sorted(timestamps):
            raise ValueError("financial ledger entries must be chronological")

    def summarize(
        self,
        *,
        currency: str,
        account_id: str | None = None,
    ) -> FinancialLedgerSummary:
        return summarize_financial_ledger(
            self.entries,
            currency=currency,
            account_id=account_id,
        )


@dataclass(frozen=True, slots=True)
class TaxProfile:
    profile_id: str
    jurisdiction: str
    residency: str
    tax_year: int
    account_type: str
    asset_classification: str
    cost_basis_method: CostBasisMethod
    loss_netting_pool: str
    base_currency: str
    rule_version: str
    effective_from: date
    effective_to: date | None
    source_reference: str
    confidence: TaxConfidence

    def __post_init__(self) -> None:
        for name in (
            "profile_id",
            "jurisdiction",
            "residency",
            "account_type",
            "asset_classification",
            "loss_netting_pool",
            "base_currency",
            "rule_version",
            "source_reference",
        ):
            _require_text(getattr(self, name), name)
        if self.tax_year < 1900:
            raise ValueError("tax_year must be at least 1900")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")


@dataclass(frozen=True, slots=True)
class TaxLot:
    lot_id: str
    account_id: str
    symbol: str
    acquired_at: datetime
    quantity: Decimal
    total_cost: Decimal
    cost_currency: str
    source_fill_id: str

    def __post_init__(self) -> None:
        for name in (
            "lot_id",
            "account_id",
            "symbol",
            "cost_currency",
            "source_fill_id",
        ):
            _require_text(getattr(self, name), name)
        _require_aware(self.acquired_at, "acquired_at")
        _require_positive(self.quantity, "quantity")
        _require_non_negative(self.total_cost, "total_cost")


@dataclass(frozen=True, slots=True)
class TaxableEvent:
    event_id: str
    occurred_at: datetime
    account_id: str
    jurisdiction: str
    tax_year: int
    event_kind: TaxableEventKind
    currency: str
    gross_amount: Decimal
    deductible_amount: Decimal
    taxable_amount: Decimal
    rule_version: str
    confidence: TaxConfidence
    source_entry_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "account_id",
            "jurisdiction",
            "currency",
            "rule_version",
        ):
            _require_text(getattr(self, name), name)
        _require_aware(self.occurred_at, "occurred_at")
        if self.tax_year < 1900:
            raise ValueError("tax_year must be at least 1900")
        _require_non_negative(self.gross_amount, "gross_amount")
        _require_non_negative(self.deductible_amount, "deductible_amount")
        _require_finite(self.taxable_amount, "taxable_amount")
        if self.taxable_amount != self.gross_amount - self.deductible_amount:
            raise ValueError("taxable_amount must equal gross_amount - deductible_amount")
        if not self.source_entry_ids:
            raise ValueError("source_entry_ids must not be empty")
        if any(not value.strip() for value in self.source_entry_ids):
            raise ValueError("source_entry_ids must contain non-empty values")


@dataclass(frozen=True, slots=True)
class TaxEstimate:
    estimate_id: str
    profile_id: str
    tax_year: int
    currency: str
    taxable_base: Decimal
    estimated_tax: Decimal
    rule_version: str
    confidence: TaxConfidence
    source_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("estimate_id", "profile_id", "currency", "rule_version"):
            _require_text(getattr(self, name), name)
        if self.tax_year < 1900:
            raise ValueError("tax_year must be at least 1900")
        _require_finite(self.taxable_base, "taxable_base")
        _require_non_negative(self.estimated_tax, "estimated_tax")
        if not self.source_event_ids:
            raise ValueError("source_event_ids must not be empty")


@dataclass(frozen=True, slots=True)
class AfterTaxSummary:
    currency: str
    economic_net_pnl: Decimal
    estimated_tax: Decimal
    estimated_after_tax_pnl: Decimal
    confidence: TaxConfidence
    review_required: bool


def summarize_financial_ledger(
    entries: Sequence[FinancialLedgerEntry],
    *,
    currency: str,
    account_id: str | None = None,
) -> FinancialLedgerSummary:
    """Aggregate signed ledger entries without implicit currency conversion."""

    _require_text(currency, "currency")
    if account_id is not None:
        _require_text(account_id, "account_id")

    selected = tuple(
        entry
        for entry in entries
        if entry.currency == currency
        and (account_id is None or entry.account_id == account_id)
    )
    gross = sum(
        (entry.amount for entry in selected if entry.kind is LedgerEntryKind.REALIZED_PNL),
        start=ZERO,
    )
    execution = sum(
        (entry.amount for entry in selected if entry.kind in _EXECUTION_KINDS),
        start=ZERO,
    )
    financing = sum(
        (entry.amount for entry in selected if entry.kind in _FINANCING_KINDS),
        start=ZERO,
    )
    tax_cash = sum(
        (entry.amount for entry in selected if entry.kind in _TAX_CASH_KINDS),
        start=ZERO,
    )
    external_cash = sum(
        (
            entry.amount
            for entry in selected
            if entry.kind is LedgerEntryKind.CASH_MOVEMENT
        ),
        start=ZERO,
    )
    economic_net = gross + execution + financing
    return FinancialLedgerSummary(
        account_id=account_id,
        currency=currency,
        gross_realized_pnl=gross,
        execution_adjustment=execution,
        financing_adjustment=financing,
        economic_net_pnl=economic_net,
        tax_cash_flow=tax_cash,
        external_cash_movement=external_cash,
        reconciled_value_change=economic_net + tax_cash + external_cash,
    )


def apply_tax_estimate(
    summary: FinancialLedgerSummary,
    estimate: TaxEstimate,
) -> AfterTaxSummary:
    """Apply a separate annual tax estimate to economic net PnL."""

    if summary.currency != estimate.currency:
        raise ValueError("financial summary and tax estimate currencies must match")
    return AfterTaxSummary(
        currency=summary.currency,
        economic_net_pnl=summary.economic_net_pnl,
        estimated_tax=estimate.estimated_tax,
        estimated_after_tax_pnl=summary.economic_net_pnl - estimate.estimated_tax,
        confidence=estimate.confidence,
        review_required=estimate.confidence is TaxConfidence.REVIEW_REQUIRED,
    )


def _validate_order_price(
    order_type: ExecutionOrderType,
    limit_price: Decimal | None,
) -> None:
    if order_type is ExecutionOrderType.LIMIT:
        if limit_price is None:
            raise ValueError("limit orders require limit_price")
        _require_positive(limit_price, "limit_price")
    elif limit_price is not None:
        raise ValueError("market orders must not set limit_price")


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
