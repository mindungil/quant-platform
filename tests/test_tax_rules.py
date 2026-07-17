from datetime import date
from decimal import Decimal

import pytest

from quant_platform.tax_rules import (
    TaxAccountType,
    TaxAmountFinality,
    TaxAssetClassification,
    TaxRateComponentKind,
    TaxRuleKey,
    TaxRulePurpose,
    TaxRuleQuery,
    TaxRuleResolutionStatus,
    build_kr_individual_tax_registry_v0,
)

D = Decimal


def test_verified_foreign_equity_rule_estimates_after_shared_deduction() -> None:
    registry = build_kr_individual_tax_registry_v0()
    key = TaxRuleKey(
        "KR",
        "KR_INDIVIDUAL",
        2026,
        TaxAccountType.GENERAL_TAXABLE,
        TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
    )

    resolution = registry.resolve(key, date(2026, 12, 31))

    assert resolution.status is TaxRuleResolutionStatus.VERIFIED
    assert resolution.rule is not None
    breakdown = resolution.rule.estimate_breakdown(
        annual_net_gain=D("10000000"),
        estimate_id="estimate-2026",
        source_event_ids=("event-1",),
    )
    assert breakdown.estimate.taxable_base == D("7500000")
    assert breakdown.estimate.estimated_tax == D("1650000.00")
    assert breakdown.finality is TaxAmountFinality.ESTIMATE_ONLY
    assert tuple(component.kind for component in breakdown.components) == (
        TaxRateComponentKind.NATIONAL_INCOME_TAX,
        TaxRateComponentKind.LOCAL_INCOME_TAX,
    )
    assert tuple(component.amount for component in breakdown.components) == (
        D("1500000.00"),
        D("150000.00"),
    )


def test_review_required_rule_never_exposes_computation() -> None:
    registry = build_kr_individual_tax_registry_v0()
    key = TaxRuleKey(
        "KR",
        "KR_INDIVIDUAL",
        2026,
        TaxAccountType.GENERAL_TAXABLE,
        TaxAssetClassification.CRYPTO_DERIVATIVE,
    )

    resolution = registry.resolve(key, date(2026, 7, 18))

    assert resolution.status is TaxRuleResolutionStatus.REVIEW_REQUIRED
    assert resolution.rule is not None
    assert resolution.rule.computation is None
    assert resolution.rule.review_reasons
    with pytest.raises(ValueError, match="unavailable"):
        resolution.rule.estimate(
            annual_net_gain=D("100"),
            estimate_id="not-allowed",
            source_event_ids=("event-1",),
        )


def test_missing_rule_is_explicit_and_does_not_fall_back() -> None:
    registry = build_kr_individual_tax_registry_v0()
    key = TaxRuleKey(
        "US",
        "US_INDIVIDUAL",
        2026,
        TaxAccountType.GENERAL_TAXABLE,
        TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
    )

    resolution = registry.resolve(key, date(2026, 1, 1))

    assert resolution.status is TaxRuleResolutionStatus.MISSING
    assert resolution.rule is None


def test_loss_or_gain_below_deduction_has_zero_estimated_tax() -> None:
    registry = build_kr_individual_tax_registry_v0()
    key = TaxRuleKey(
        "KR",
        "KR_INDIVIDUAL",
        2026,
        TaxAccountType.GENERAL_TAXABLE,
        TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
    )
    rule = registry.resolve(key, date(2026, 1, 1)).rule
    assert rule is not None

    for value in (D("-1"), D("2500000")):
        estimate = rule.estimate(
            annual_net_gain=value,
            estimate_id=f"estimate-{value}",
            source_event_ids=("event-1",),
        )
        assert estimate.taxable_base == D("0")
        assert estimate.estimated_tax == D("0.00")


def test_query_derives_tax_year_from_transaction_date() -> None:
    registry = build_kr_individual_tax_registry_v0()
    query = TaxRuleQuery(
        jurisdiction="KR",
        residency="KR_INDIVIDUAL",
        account_type=TaxAccountType.GENERAL_TAXABLE,
        asset_classification=TaxAssetClassification.DOMESTIC_LISTED_EQUITY,
        purpose=TaxRulePurpose.TRANSACTION_LEVY,
        venue="KOSPI",
    )

    resolution = registry.resolve_for_date(query, date(2026, 1, 1))

    assert resolution.key.tax_year == 2026
    assert resolution.effective_on == date(2026, 1, 1)
    assert resolution.status is TaxRuleResolutionStatus.VERIFIED


def test_wrong_year_date_is_rejected() -> None:
    registry = build_kr_individual_tax_registry_v0()
    key = TaxRuleKey(
        "KR",
        "KR_INDIVIDUAL",
        2026,
        TaxAccountType.GENERAL_TAXABLE,
        TaxAssetClassification.DERIVATIVE,
    )

    with pytest.raises(ValueError, match="year"):
        registry.resolve(key, date(2025, 12, 31))


def test_registry_sources_and_profiles_have_explicit_intervals() -> None:
    registry = build_kr_individual_tax_registry_v0()

    assert registry.version == "kr-tax-registry-v0.2.0"
    assert registry.source_as_of == date(2026, 7, 18)
    for rule in registry.rules:
        assert rule.profile.effective_from == date(rule.key.tax_year, 1, 1)
        assert rule.profile.effective_to == date(rule.key.tax_year, 12, 31)
        assert rule.profile.loss_netting_pool
        assert all(
            source.covers(rule.profile.effective_from, rule.profile.effective_to)
            for source in rule.sources
        )
