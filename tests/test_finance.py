from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.finance import (
    CostBasisMethod,
    ExecutionIntent,
    ExecutionOrderType,
    ExecutionRealityProfile,
    FinancialLedger,
    FinancialLedgerEntry,
    LedgerEntryKind,
    OrderSide,
    TaxConfidence,
    TaxEstimate,
    TaxProfile,
    TaxableEvent,
    TaxableEventKind,
    apply_tax_estimate,
)

D = Decimal
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _entry(index: int, kind: LedgerEntryKind, amount: str) -> FinancialLedgerEntry:
    return FinancialLedgerEntry(
        entry_id=f"entry-{index}",
        occurred_at=NOW + timedelta(seconds=index),
        account_id="paper-main",
        currency="USD",
        kind=kind,
        amount=D(amount),
        symbol="BTCUSDT",
    )


def test_ledger_summary_matches_hand_calculation() -> None:
    ledger = FinancialLedger(
        entries=(
            _entry(1, LedgerEntryKind.CASH_MOVEMENT, "1000"),
            _entry(2, LedgerEntryKind.REALIZED_PNL, "100"),
            _entry(3, LedgerEntryKind.COMMISSION, "-2"),
            _entry(4, LedgerEntryKind.SLIPPAGE, "-1"),
            _entry(5, LedgerEntryKind.FUNDING, "3"),
            _entry(6, LedgerEntryKind.BORROW_INTEREST, "-0.5"),
            _entry(7, LedgerEntryKind.TRANSACTION_TAX, "-0.2"),
            _entry(8, LedgerEntryKind.TAX_WITHHOLDING, "-10"),
        )
    )

    summary = ledger.summarize(currency="USD", account_id="paper-main")

    assert summary.gross_realized_pnl == D("100")
    assert summary.execution_adjustment == D("-3.2")
    assert summary.financing_adjustment == D("2.5")
    assert summary.economic_net_pnl == D("99.3")
    assert summary.tax_cash_flow == D("-10")
    assert summary.external_cash_movement == D("1000")
    assert summary.reconciled_value_change == D("1089.3")


def test_annual_tax_estimate_is_not_folded_into_execution_costs() -> None:
    summary = FinancialLedger(
        entries=(
            _entry(1, LedgerEntryKind.REALIZED_PNL, "100"),
            _entry(2, LedgerEntryKind.COMMISSION, "-2"),
        )
    ).summarize(currency="USD")
    estimate = TaxEstimate(
        estimate_id="tax-2026",
        profile_id="kr-individual-2026",
        tax_year=2026,
        currency="USD",
        taxable_base=D("98"),
        estimated_tax=D("20"),
        rule_version="kr-v0",
        confidence=TaxConfidence.REVIEW_REQUIRED,
        source_event_ids=("tax-event-1",),
    )

    after_tax = apply_tax_estimate(summary, estimate)

    assert summary.execution_adjustment == D("-2")
    assert summary.economic_net_pnl == D("98")
    assert after_tax.estimated_after_tax_pnl == D("78")
    assert after_tax.review_required is True


def test_taxable_event_requires_exact_taxable_amount_identity() -> None:
    with pytest.raises(ValueError, match="gross_amount - deductible_amount"):
        TaxableEvent(
            event_id="tax-event-1",
            occurred_at=NOW,
            account_id="paper-main",
            jurisdiction="KR",
            tax_year=2026,
            event_kind=TaxableEventKind.DISPOSAL,
            currency="KRW",
            gross_amount=D("1000"),
            deductible_amount=D("700"),
            taxable_amount=D("301"),
            rule_version="kr-v0",
            confidence=TaxConfidence.ASSUMED,
            source_entry_ids=("entry-1",),
        )


def test_ledger_rejects_duplicate_ids_and_non_chronological_entries() -> None:
    first = _entry(1, LedgerEntryKind.REALIZED_PNL, "1")
    duplicate = FinancialLedgerEntry(
        entry_id=first.entry_id,
        occurred_at=NOW + timedelta(seconds=2),
        account_id=first.account_id,
        currency=first.currency,
        kind=LedgerEntryKind.COMMISSION,
        amount=D("-0.1"),
    )

    with pytest.raises(ValueError, match="unique"):
        FinancialLedger(entries=(first, duplicate))
    with pytest.raises(ValueError, match="chronological"):
        FinancialLedger(entries=(_entry(2, LedgerEntryKind.REALIZED_PNL, "1"), first))


def test_cost_entries_use_signed_account_perspective() -> None:
    with pytest.raises(ValueError, match="non-positive"):
        _entry(1, LedgerEntryKind.COMMISSION, "1")
    with pytest.raises(ValueError, match="non-negative"):
        _entry(1, LedgerEntryKind.REBATE, "-1")


def test_execution_profile_uses_decimal_rates_and_explicit_models() -> None:
    profile = ExecutionRealityProfile(
        profile_id="binance-usdm-standard",
        venue="BINANCE",
        market="USDM_PERPETUAL",
        account_type="CROSS_MARGIN",
        settlement_currency="USDT",
        maker_fee_rate=D("0.0002"),
        taker_fee_rate=D("0.0005"),
        minimum_notional=D("5"),
        quantity_step=D("0.001"),
        price_tick=D("0.1"),
        funding_model="binance-realized-funding-v1",
        margin_model="cross-margin-no-liquidation-v0",
    )

    assert profile.taker_fee_rate == D("0.0005")
    assert profile.funding_model == "binance-realized-funding-v1"

    with pytest.raises(ValueError, match="greater than -1"):
        ExecutionRealityProfile(
            profile_id="bad",
            venue="X",
            market="SPOT",
            account_type="CASH",
            settlement_currency="USD",
            taker_fee_rate=D("-1"),
        )


def test_tax_profile_keeps_rule_version_source_and_review_state() -> None:
    profile = TaxProfile(
        profile_id="kr-crypto-derivative-2026",
        jurisdiction="KR",
        residency="KR_INDIVIDUAL",
        tax_year=2026,
        account_type="OFFSHORE_EXCHANGE",
        asset_classification="CRYPTO_DERIVATIVE_UNRESOLVED",
        cost_basis_method=CostBasisMethod.UNSPECIFIED,
        loss_netting_pool="UNRESOLVED",
        base_currency="KRW",
        rule_version="kr-v0",
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        source_reference="official-source-placeholder",
        confidence=TaxConfidence.REVIEW_REQUIRED,
    )

    assert profile.confidence is TaxConfidence.REVIEW_REQUIRED
    assert profile.rule_version == "kr-v0"


def test_market_order_does_not_accept_limit_price() -> None:
    with pytest.raises(ValueError, match="must not set limit_price"):
        ExecutionIntent(
            intent_id="intent-1",
            strategy_id="strategy-1",
            account_id="paper-main",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=D("1"),
            order_type=ExecutionOrderType.MARKET,
            created_at=NOW,
            limit_price=D("100"),
        )
