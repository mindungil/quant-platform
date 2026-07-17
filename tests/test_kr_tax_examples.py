from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quant_platform import LedgerEntryKind
from quant_platform.tax_rules import (
    TaxAccountType,
    TaxAmountFinality,
    TaxAssetClassification,
    TaxRuleKey,
    TaxRulePurpose,
    TaxRuleQuery,
    TaxRuleRegistry,
    TaxRuleResolutionStatus,
    build_kr_individual_tax_registry_v0,
    transaction_levy_ledger_entry,
)

D = Decimal


def _annual_rule(asset: TaxAssetClassification, year: int = 2026):
    registry = build_kr_individual_tax_registry_v0()
    resolution = registry.resolve(
        TaxRuleKey(
            "KR",
            "KR_INDIVIDUAL",
            year,
            TaxAccountType.GENERAL_TAXABLE,
            asset,
        ),
        date(year, 12, 31),
    )
    assert resolution.rule is not None
    return resolution.rule


def _levy_rule(venue: str, on_date: date):
    registry = build_kr_individual_tax_registry_v0()
    resolution = registry.resolve_for_date(
        TaxRuleQuery(
            jurisdiction="KR",
            residency="KR_INDIVIDUAL",
            account_type=TaxAccountType.GENERAL_TAXABLE,
            asset_classification=TaxAssetClassification.DOMESTIC_LISTED_EQUITY,
            purpose=TaxRulePurpose.TRANSACTION_LEVY,
            venue=venue,
        ),
        on_date,
    )
    assert resolution.rule is not None
    return resolution.rule


def test_hand_example_kospi_2026_securities_transaction_tax_component() -> None:
    rule = _levy_rule("KOSPI", date(2026, 1, 1))

    estimate = rule.estimate_transaction_levy(
        taxable_base=D("10000000"),
        estimate_id="kospi-sale",
        transaction_date=date(2026, 1, 1),
        source_reference_ids=("broker-fill-1",),
    )

    assert estimate.total_estimated_levy == D("5000.0000")
    assert estimate.finality is TaxAmountFinality.ESTIMATE_ONLY


def test_hand_example_kosdaq_2026_securities_transaction_tax_component() -> None:
    rule = _levy_rule("KOSDAQ", date(2026, 7, 18))

    estimate = rule.estimate_transaction_levy(
        taxable_base=D("10000000"),
        estimate_id="kosdaq-sale",
        transaction_date=date(2026, 7, 18),
        source_reference_ids=("broker-fill-2",),
    )

    assert estimate.total_estimated_levy == D("20000.0000")


def test_hand_example_derivative_pool_deduction_and_components() -> None:
    rule = _annual_rule(TaxAssetClassification.DERIVATIVE)

    breakdown = rule.estimate_breakdown(
        annual_net_gain=D("130000000"),
        estimate_id="derivative-2026",
        source_event_ids=("derivative-ledger",),
    )

    assert rule.profile.loss_netting_pool == "KR_DERIVATIVE_CAPITAL_GAINS_POOL_2026"
    assert breakdown.estimate.taxable_base == D("127500000")
    assert tuple(component.amount for component in breakdown.components) == (
        D("12750000.00"),
        D("1275000.00"),
    )
    assert breakdown.estimate.estimated_tax == D("14025000.00")


def test_virtual_asset_spot_future_rule_does_not_overwrite_2026() -> None:
    registry = build_kr_individual_tax_registry_v0()
    query = TaxRuleQuery(
        jurisdiction="KR",
        residency="KR_INDIVIDUAL",
        account_type=TaxAccountType.GENERAL_TAXABLE,
        asset_classification=TaxAssetClassification.VIRTUAL_ASSET_SPOT,
    )

    before = registry.resolve_for_date(query, date(2026, 12, 31))
    effective = registry.resolve_for_date(query, date(2027, 1, 1))
    after = registry.resolve_for_date(query, date(2027, 1, 2))

    assert before.status is TaxRuleResolutionStatus.REVIEW_REQUIRED
    assert effective.status is TaxRuleResolutionStatus.REVIEW_REQUIRED
    assert after.status is TaxRuleResolutionStatus.REVIEW_REQUIRED
    assert before.rule is not None
    assert effective.rule is not None
    assert after.rule is not None
    assert before.rule.version == "kr-virtual-asset-2026-v1"
    assert effective.rule.version == "kr-virtual-asset-2027-v1"
    assert after.rule.version == "kr-virtual-asset-2027-v1"
    assert before.rule.version != effective.rule.version


def test_kospi_rate_boundary_day_before_on_and_after() -> None:
    old_rule = _levy_rule("KOSPI", date(2025, 12, 31))
    new_rule = _levy_rule("KOSPI", date(2026, 1, 1))
    after_rule = _levy_rule("KOSPI", date(2026, 1, 2))

    old = old_rule.estimate_transaction_levy(
        taxable_base=D("10000000"),
        estimate_id="old",
        transaction_date=date(2025, 12, 31),
        source_reference_ids=("old-fill",),
    )
    new = new_rule.estimate_transaction_levy(
        taxable_base=D("10000000"),
        estimate_id="new",
        transaction_date=date(2026, 1, 1),
        source_reference_ids=("new-fill",),
    )
    after = after_rule.estimate_transaction_levy(
        taxable_base=D("10000000"),
        estimate_id="after",
        transaction_date=date(2026, 1, 2),
        source_reference_ids=("after-fill",),
    )

    assert old.total_estimated_levy == D("0")
    assert new.total_estimated_levy == D("5000.0000")
    assert after.total_estimated_levy == D("5000.0000")


def test_transaction_levy_becomes_negative_ledger_entry() -> None:
    rule = _levy_rule("KOSPI", date(2026, 1, 1))
    estimate = rule.estimate_transaction_levy(
        taxable_base=D("10000000"),
        estimate_id="levy-1",
        transaction_date=date(2026, 1, 1),
        source_reference_ids=("fill-1",),
    )

    entry = transaction_levy_ledger_entry(
        estimate,
        entry_id="tax-entry-1",
        occurred_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        account_id="account-1",
        symbol="005930",
    )

    assert entry.kind is LedgerEntryKind.TRANSACTION_TAX
    assert entry.amount == D("-5000.0000")
    assert "estimate_only" in entry.description


def test_overlapping_rules_for_one_exact_key_are_rejected() -> None:
    registry = build_kr_individual_tax_registry_v0()
    original = _annual_rule(TaxAssetClassification.DERIVATIVE)
    duplicate = replace(original, rule_id="duplicate-derivative-rule")

    with pytest.raises(ValueError, match="overlap"):
        TaxRuleRegistry(
            registry_id="invalid",
            version="invalid",
            source_as_of=registry.source_as_of,
            rules=(original, duplicate),
        )


def test_transaction_levy_purpose_does_not_fall_back_to_income_tax() -> None:
    registry = build_kr_individual_tax_registry_v0()
    missing = registry.resolve(
        TaxRuleKey(
            jurisdiction="KR",
            residency="KR_INDIVIDUAL",
            tax_year=2026,
            account_type=TaxAccountType.GENERAL_TAXABLE,
            asset_classification=TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
            purpose=TaxRulePurpose.TRANSACTION_LEVY,
            venue="NASDAQ",
        ),
        date(2026, 1, 1),
    )

    assert missing.status is TaxRuleResolutionStatus.MISSING
    assert missing.rule is None
