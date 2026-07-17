from datetime import date
from decimal import Decimal

import pytest

from quant_platform.tax_rules import (
    TaxAccountType,
    TaxAssetClassification,
    TaxRuleKey,
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

    resolution = registry.resolve(key)

    assert resolution.status is TaxRuleResolutionStatus.VERIFIED
    assert resolution.rule is not None
    assert resolution.rule.profile.effective_from == date(2026, 1, 1)
    assert resolution.rule.sources[0].source_as_of == date(2026, 7, 18)
    estimate = resolution.rule.estimate(
        annual_net_gain=D("10000000"),
        estimate_id="estimate-2026",
        source_event_ids=("event-1",),
    )
    assert estimate.taxable_base == D("7500000")
    assert estimate.estimated_tax == D("1650000.00")


def test_review_required_rule_never_exposes_computation() -> None:
    registry = build_kr_individual_tax_registry_v0()
    key = TaxRuleKey(
        "KR",
        "KR_INDIVIDUAL",
        2026,
        TaxAccountType.GENERAL_TAXABLE,
        TaxAssetClassification.CRYPTO_DERIVATIVE,
    )

    resolution = registry.resolve(key)

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

    resolution = registry.resolve(key)

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
    rule = registry.resolve(key).rule
    assert rule is not None

    for value in (D("-1"), D("2500000")):
        estimate = rule.estimate(
            annual_net_gain=value,
            estimate_id=f"estimate-{value}",
            source_event_ids=("event-1",),
        )
        assert estimate.taxable_base == D("0")
        assert estimate.estimated_tax == D("0.00")


def test_sources_cover_the_tax_year_and_registry_as_of_date() -> None:
    registry = build_kr_individual_tax_registry_v0()

    assert registry.source_as_of == date(2026, 7, 18)
    for rule in registry.rules:
        assert rule.profile.effective_from == date(2026, 1, 1)
        assert rule.profile.effective_to == date(2026, 12, 31)
        assert all(source.applies_to(2026) for source in rule.sources)
