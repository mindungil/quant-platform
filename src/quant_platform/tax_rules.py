"""Versioned tax-rule registry contracts and the Korean resident v0 registry."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from .finance import (
    CostBasisMethod,
    FinancialLedgerEntry,
    LedgerEntryKind,
    TaxConfidence,
    TaxEstimate,
    TaxProfile,
)

ZERO = Decimal("0")
ONE = Decimal("1")


class TaxRuleResolutionStatus(StrEnum):
    VERIFIED = "verified"
    REVIEW_REQUIRED = "review_required"
    MISSING = "missing"


class TaxRulePurpose(StrEnum):
    ANNUAL_INCOME_TAX = "ANNUAL_INCOME_TAX"
    TRANSACTION_LEVY = "TRANSACTION_LEVY"


class TaxAmountFinality(StrEnum):
    ESTIMATE_ONLY = "estimate_only"


class TaxAccountType(StrEnum):
    GENERAL_TAXABLE = "GENERAL_TAXABLE"
    ISA = "ISA"
    PENSION = "PENSION"
    CORPORATE = "CORPORATE"
    UNKNOWN = "UNKNOWN"


class TaxAssetClassification(StrEnum):
    FOREIGN_LISTED_EQUITY_NON_SME = "FOREIGN_LISTED_EQUITY_NON_SME"
    FOREIGN_LISTED_EQUITY_SME_OR_UNKNOWN = "FOREIGN_LISTED_EQUITY_SME_OR_UNKNOWN"
    DOMESTIC_LISTED_EQUITY = "DOMESTIC_LISTED_EQUITY"
    DERIVATIVE = "DERIVATIVE"
    VIRTUAL_ASSET_SPOT = "VIRTUAL_ASSET_SPOT"
    CRYPTO_DERIVATIVE = "CRYPTO_DERIVATIVE"


class TaxRateComponentKind(StrEnum):
    NATIONAL_INCOME_TAX = "NATIONAL_INCOME_TAX"
    LOCAL_INCOME_TAX = "LOCAL_INCOME_TAX"
    SECURITIES_TRANSACTION_TAX = "SECURITIES_TRANSACTION_TAX"


@dataclass(frozen=True, slots=True)
class TaxRuleKey:
    jurisdiction: str
    residency: str
    tax_year: int
    account_type: TaxAccountType
    asset_classification: TaxAssetClassification
    purpose: TaxRulePurpose = TaxRulePurpose.ANNUAL_INCOME_TAX
    venue: str = "ANY"

    def __post_init__(self) -> None:
        _require_text(self.jurisdiction, "jurisdiction")
        _require_text(self.residency, "residency")
        _require_text(self.venue, "venue")
        if self.tax_year < 1900:
            raise ValueError("tax_year must be at least 1900")


@dataclass(frozen=True, slots=True)
class TaxRuleQuery:
    jurisdiction: str
    residency: str
    account_type: TaxAccountType
    asset_classification: TaxAssetClassification
    purpose: TaxRulePurpose = TaxRulePurpose.ANNUAL_INCOME_TAX
    venue: str = "ANY"

    def __post_init__(self) -> None:
        _require_text(self.jurisdiction, "jurisdiction")
        _require_text(self.residency, "residency")
        _require_text(self.venue, "venue")

    def key_for(self, on_date: date) -> TaxRuleKey:
        return TaxRuleKey(
            jurisdiction=self.jurisdiction,
            residency=self.residency,
            tax_year=on_date.year,
            account_type=self.account_type,
            asset_classification=self.asset_classification,
            purpose=self.purpose,
            venue=self.venue,
        )


@dataclass(frozen=True, slots=True)
class TaxRuleSource:
    source_id: str
    authority: str
    title: str
    reference_url: str
    source_as_of: date
    effective_from: date
    effective_to: date | None = None

    def __post_init__(self) -> None:
        for name in ("source_id", "authority", "title", "reference_url"):
            _require_text(getattr(self, name), name)
        if not self.reference_url.startswith("https://"):
            raise ValueError("reference_url must use https")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")

    def applies_to(self, tax_year: int) -> bool:
        period_start = date(tax_year, 1, 1)
        period_end = date(tax_year, 12, 31)
        return self.covers(period_start, period_end)

    def covers(self, effective_from: date, effective_to: date | None) -> bool:
        if self.effective_from > effective_from:
            return False
        if effective_to is None:
            return self.effective_to is None
        return self.effective_to is None or self.effective_to >= effective_to


@dataclass(frozen=True, slots=True)
class TaxRateComponent:
    component_id: str
    kind: TaxRateComponentKind
    rate: Decimal

    def __post_init__(self) -> None:
        _require_text(self.component_id, "component_id")
        _require_rate(self.rate, "rate")


@dataclass(frozen=True, slots=True)
class TaxComponentAmount:
    component_id: str
    kind: TaxRateComponentKind
    rate: Decimal
    amount: Decimal

    def __post_init__(self) -> None:
        _require_text(self.component_id, "component_id")
        _require_rate(self.rate, "rate")
        _require_non_negative(self.amount, "amount")


@dataclass(frozen=True, slots=True)
class TaxEstimateBreakdown:
    estimate: TaxEstimate
    components: tuple[TaxComponentAmount, ...]
    finality: TaxAmountFinality = TaxAmountFinality.ESTIMATE_ONLY

    def __post_init__(self) -> None:
        if not self.components:
            raise ValueError("components must not be empty")
        total = sum((component.amount for component in self.components), start=ZERO)
        if total != self.estimate.estimated_tax:
            raise ValueError("component amounts must sum to estimated_tax")


@dataclass(frozen=True, slots=True)
class TransactionLevyEstimate:
    estimate_id: str
    profile_id: str
    transaction_date: date
    currency: str
    taxable_base: Decimal
    components: tuple[TaxComponentAmount, ...]
    total_estimated_levy: Decimal
    rule_version: str
    confidence: TaxConfidence
    source_reference_ids: tuple[str, ...]
    finality: TaxAmountFinality = TaxAmountFinality.ESTIMATE_ONLY

    def __post_init__(self) -> None:
        for name in ("estimate_id", "profile_id", "currency", "rule_version"):
            _require_text(getattr(self, name), name)
        _require_non_negative(self.taxable_base, "taxable_base")
        _require_non_negative(self.total_estimated_levy, "total_estimated_levy")
        if not self.components:
            raise ValueError("components must not be empty")
        component_total = sum((component.amount for component in self.components), start=ZERO)
        if component_total != self.total_estimated_levy:
            raise ValueError("component amounts must sum to total_estimated_levy")
        if not self.source_reference_ids or any(
            not value.strip() for value in self.source_reference_ids
        ):
            raise ValueError("source_reference_ids must contain non-empty values")


@dataclass(frozen=True, slots=True)
class TaxComputationRule:
    currency: str
    annual_basic_deduction: Decimal
    national_rate: Decimal
    local_rate: Decimal
    input_basis: str

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_text(self.input_basis, "input_basis")
        _require_non_negative(self.annual_basic_deduction, "annual_basic_deduction")
        _require_rate(self.national_rate, "national_rate")
        _require_rate(self.local_rate, "local_rate")

    @property
    def combined_rate(self) -> Decimal:
        return self.national_rate + self.local_rate

    @property
    def rate_components(self) -> tuple[TaxRateComponent, ...]:
        return (
            TaxRateComponent(
                component_id="national-income-tax",
                kind=TaxRateComponentKind.NATIONAL_INCOME_TAX,
                rate=self.national_rate,
            ),
            TaxRateComponent(
                component_id="local-income-tax",
                kind=TaxRateComponentKind.LOCAL_INCOME_TAX,
                rate=self.local_rate,
            ),
        )

    def estimate(
        self,
        *,
        annual_net_gain: Decimal,
        estimate_id: str,
        profile: TaxProfile,
        source_event_ids: Sequence[str],
    ) -> TaxEstimate:
        return self.estimate_breakdown(
            annual_net_gain=annual_net_gain,
            estimate_id=estimate_id,
            profile=profile,
            source_event_ids=source_event_ids,
        ).estimate

    def estimate_breakdown(
        self,
        *,
        annual_net_gain: Decimal,
        estimate_id: str,
        profile: TaxProfile,
        source_event_ids: Sequence[str],
    ) -> TaxEstimateBreakdown:
        _require_text(estimate_id, "estimate_id")
        _require_finite(annual_net_gain, "annual_net_gain")
        identifiers = _require_identifiers(source_event_ids, "source_event_ids")
        taxable_base = max(ZERO, annual_net_gain - self.annual_basic_deduction)
        components = tuple(
            TaxComponentAmount(
                component_id=component.component_id,
                kind=component.kind,
                rate=component.rate,
                amount=taxable_base * component.rate,
            )
            for component in self.rate_components
        )
        estimated_tax = sum((component.amount for component in components), start=ZERO)
        estimate = TaxEstimate(
            estimate_id=estimate_id,
            profile_id=profile.profile_id,
            tax_year=profile.tax_year,
            currency=self.currency,
            taxable_base=taxable_base,
            estimated_tax=estimated_tax,
            rule_version=profile.rule_version,
            confidence=TaxConfidence.CONFIRMED,
            source_event_ids=identifiers,
        )
        return TaxEstimateBreakdown(estimate=estimate, components=components)


@dataclass(frozen=True, slots=True)
class TransactionLevyComputationRule:
    currency: str
    rate_components: tuple[TaxRateComponent, ...]
    input_basis: str

    def __post_init__(self) -> None:
        _require_text(self.currency, "currency")
        _require_text(self.input_basis, "input_basis")
        if not self.rate_components:
            raise ValueError("rate_components must not be empty")
        identifiers = tuple(component.component_id for component in self.rate_components)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("rate component IDs must be unique")

    def estimate(
        self,
        *,
        taxable_base: Decimal,
        estimate_id: str,
        profile: TaxProfile,
        transaction_date: date,
        source_reference_ids: Sequence[str],
    ) -> TransactionLevyEstimate:
        _require_text(estimate_id, "estimate_id")
        _require_non_negative(taxable_base, "taxable_base")
        identifiers = _require_identifiers(source_reference_ids, "source_reference_ids")
        components = tuple(
            TaxComponentAmount(
                component_id=component.component_id,
                kind=component.kind,
                rate=component.rate,
                amount=taxable_base * component.rate,
            )
            for component in self.rate_components
        )
        total = sum((component.amount for component in components), start=ZERO)
        return TransactionLevyEstimate(
            estimate_id=estimate_id,
            profile_id=profile.profile_id,
            transaction_date=transaction_date,
            currency=self.currency,
            taxable_base=taxable_base,
            components=components,
            total_estimated_levy=total,
            rule_version=profile.rule_version,
            confidence=TaxConfidence.CONFIRMED,
            source_reference_ids=identifiers,
        )


TaxRuleComputation = TaxComputationRule | TransactionLevyComputationRule


@dataclass(frozen=True, slots=True)
class TaxRule:
    rule_id: str
    version: str
    key: TaxRuleKey
    status: TaxRuleResolutionStatus
    profile: TaxProfile
    sources: tuple[TaxRuleSource, ...]
    computation: TaxRuleComputation | None = None
    review_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "rule_id")
        _require_text(self.version, "version")
        if not self.sources:
            raise ValueError("sources must not be empty")
        if any(
            not source.covers(self.profile.effective_from, self.profile.effective_to)
            for source in self.sources
        ):
            raise ValueError("every source must cover the rule effective interval")
        expected = (
            self.key.jurisdiction,
            self.key.residency,
            self.key.tax_year,
            self.key.account_type.value,
            self.key.asset_classification.value,
            self.version,
        )
        actual = (
            self.profile.jurisdiction,
            self.profile.residency,
            self.profile.tax_year,
            self.profile.account_type,
            self.profile.asset_classification,
            self.profile.rule_version,
        )
        if actual != expected:
            raise ValueError("profile fields must match the rule key and version")
        if not self.is_effective(date(self.key.tax_year, 1, 1)) and not self.is_effective(
            date(self.key.tax_year, 12, 31)
        ):
            raise ValueError("rule effective interval must intersect its tax year")
        if self.status is TaxRuleResolutionStatus.VERIFIED:
            if self.computation is None:
                raise ValueError("verified rules require a computation")
            if self.review_reasons:
                raise ValueError("verified rules must not contain review reasons")
            if self.profile.confidence is not TaxConfidence.CONFIRMED:
                raise ValueError("verified profiles must use confirmed confidence")
            if self.key.purpose is TaxRulePurpose.ANNUAL_INCOME_TAX and not isinstance(
                self.computation, TaxComputationRule
            ):
                raise ValueError("annual-income-tax rules require TaxComputationRule")
            if self.key.purpose is TaxRulePurpose.TRANSACTION_LEVY and not isinstance(
                self.computation, TransactionLevyComputationRule
            ):
                raise ValueError("transaction-levy rules require TransactionLevyComputationRule")
        elif self.status is TaxRuleResolutionStatus.REVIEW_REQUIRED:
            if self.computation is not None:
                raise ValueError("review-required rules must not expose a computation")
            if not self.review_reasons or any(not value.strip() for value in self.review_reasons):
                raise ValueError("review-required rules need explicit reasons")
            if self.profile.confidence is not TaxConfidence.REVIEW_REQUIRED:
                raise ValueError("review-required profiles must use review_required confidence")
        else:
            raise ValueError("stored rules must be verified or review_required")

    def is_effective(self, on_date: date) -> bool:
        return self.profile.effective_from <= on_date and (
            self.profile.effective_to is None or on_date <= self.profile.effective_to
        )

    def estimate(
        self,
        *,
        annual_net_gain: Decimal,
        estimate_id: str,
        source_event_ids: Sequence[str],
    ) -> TaxEstimate:
        return self.estimate_breakdown(
            annual_net_gain=annual_net_gain,
            estimate_id=estimate_id,
            source_event_ids=source_event_ids,
        ).estimate

    def estimate_breakdown(
        self,
        *,
        annual_net_gain: Decimal,
        estimate_id: str,
        source_event_ids: Sequence[str],
    ) -> TaxEstimateBreakdown:
        if self.status is not TaxRuleResolutionStatus.VERIFIED or not isinstance(
            self.computation, TaxComputationRule
        ):
            raise ValueError("annual tax estimate is unavailable until this rule is verified")
        return self.computation.estimate_breakdown(
            annual_net_gain=annual_net_gain,
            estimate_id=estimate_id,
            profile=self.profile,
            source_event_ids=source_event_ids,
        )

    def estimate_transaction_levy(
        self,
        *,
        taxable_base: Decimal,
        estimate_id: str,
        transaction_date: date,
        source_reference_ids: Sequence[str],
    ) -> TransactionLevyEstimate:
        if transaction_date.year != self.key.tax_year or not self.is_effective(transaction_date):
            raise ValueError("transaction_date is outside this rule's effective interval")
        if self.status is not TaxRuleResolutionStatus.VERIFIED or not isinstance(
            self.computation, TransactionLevyComputationRule
        ):
            raise ValueError("transaction levy estimate is unavailable until this rule is verified")
        return self.computation.estimate(
            taxable_base=taxable_base,
            estimate_id=estimate_id,
            profile=self.profile,
            transaction_date=transaction_date,
            source_reference_ids=source_reference_ids,
        )


@dataclass(frozen=True, slots=True)
class TaxRuleResolution:
    status: TaxRuleResolutionStatus
    key: TaxRuleKey
    effective_on: date
    rule: TaxRule | None
    message: str

    def __post_init__(self) -> None:
        _require_text(self.message, "message")
        if self.effective_on.year != self.key.tax_year:
            raise ValueError("effective_on year must match the rule key tax year")
        if self.status is TaxRuleResolutionStatus.MISSING:
            if self.rule is not None:
                raise ValueError("missing resolutions must not contain a rule")
        elif self.rule is None or self.rule.status is not self.status:
            raise ValueError("resolution status must match the resolved rule")


@dataclass(frozen=True, slots=True)
class TaxRuleRegistry:
    registry_id: str
    version: str
    source_as_of: date
    rules: tuple[TaxRule, ...]

    def __post_init__(self) -> None:
        _require_text(self.registry_id, "registry_id")
        _require_text(self.version, "version")
        if not self.rules:
            raise ValueError("rules must not be empty")
        rule_ids = tuple(rule.rule_id for rule in self.rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("tax rule IDs must be unique")
        if any(
            source.source_as_of > self.source_as_of
            for rule in self.rules
            for source in rule.sources
        ):
            raise ValueError("registry source_as_of must cover every source")
        for index, first in enumerate(self.rules):
            for second in self.rules[index + 1 :]:
                if first.key == second.key and _intervals_overlap(first, second):
                    raise ValueError("tax rule effective intervals must not overlap for one key")

    def resolve(self, key: TaxRuleKey, on_date: date | None = None) -> TaxRuleResolution:
        effective_on = on_date or date(key.tax_year, 1, 1)
        if effective_on.year != key.tax_year:
            raise ValueError("on_date year must match the rule key tax year")
        matches = tuple(
            rule for rule in self.rules if rule.key == key and rule.is_effective(effective_on)
        )
        if len(matches) > 1:
            raise ValueError("multiple tax rules match the exact key and date")
        if not matches:
            return TaxRuleResolution(
                TaxRuleResolutionStatus.MISSING,
                key,
                effective_on,
                None,
                "no rule is registered for this exact residence/date/account/product/purpose/venue",
            )
        rule = matches[0]
        if rule.status is TaxRuleResolutionStatus.VERIFIED:
            message = "verified rule resolved; automatic estimate is available"
        else:
            message = "expert review is required before any automatic estimate"
        return TaxRuleResolution(rule.status, key, effective_on, rule, message)

    def resolve_for_date(self, query: TaxRuleQuery, on_date: date) -> TaxRuleResolution:
        return self.resolve(query.key_for(on_date), on_date)


def transaction_levy_ledger_entry(
    estimate: TransactionLevyEstimate,
    *,
    entry_id: str,
    occurred_at: datetime,
    account_id: str,
    symbol: str,
    related_id: str | None = None,
) -> FinancialLedgerEntry:
    if occurred_at.date() != estimate.transaction_date:
        raise ValueError("occurred_at date must match the levy transaction_date")
    return FinancialLedgerEntry(
        entry_id=entry_id,
        occurred_at=occurred_at,
        account_id=account_id,
        currency=estimate.currency,
        kind=LedgerEntryKind.TRANSACTION_TAX,
        amount=-estimate.total_estimated_levy,
        symbol=symbol,
        related_id=related_id or estimate.estimate_id,
        description=(
            f"estimated transaction levy {estimate.rule_version}; "
            f"finality={estimate.finality.value}"
        ),
    )


def build_kr_individual_tax_registry_v0() -> TaxRuleRegistry:
    source_as_of = date(2026, 7, 18)

    stock_scope_2026 = _source(
        "kr-income-tax-act-stock-scope-2026",
        "Income Tax Act Article 94 and Enforcement Decree Article 157-3",
        "https://www.law.go.kr/LSW/LsiJoLinkP.do?docType=JO&joNo=002000003&languageType=KO&lsNm=%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95&paras=1",
        source_as_of,
        2026,
    )
    stock_deduction_2026 = _source(
        "kr-income-tax-act-103-2026",
        "Income Tax Act Article 103 annual capital-gains basic deduction",
        "https://law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000383716",
        source_as_of,
        2026,
    )
    stock_rate_2026 = _source(
        "kr-income-tax-act-104-2026",
        "Income Tax Act Article 104(1)(12) foreign-share rates",
        "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001565&lsJoLnkSeq=1000820745",
        source_as_of,
        2026,
    )
    stock_local_rate_2026 = _source(
        "kr-local-tax-act-103-3-2026",
        "Local Tax Act Article 103-3(1)(12) foreign-share local-income-tax rates",
        "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001649&lsJoLnkSeq=1000899162&print=print",
        source_as_of,
        2026,
    )
    nts_stock_guide_2026 = _source(
        "kr-nts-capital-gains-filing-2026",
        "2026 capital-gains filing guide and domestic/foreign stock netting examples",
        "https://nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350890",
        source_as_of,
        2026,
    )
    derivative_law_2026 = _source(
        "kr-income-tax-act-derivative-2026",
        "Income Tax Act derivative capital-gains rate and Enforcement Decree elastic rate",
        "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001565&lsJoLnkSeq=1000890464",
        source_as_of,
        2026,
    )
    derivative_local_2026 = _source(
        "kr-local-tax-decree-derivative-2026",
        "Local Tax Act Enforcement Decree derivative local-income-tax rate",
        "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001649&lsJoLnkSeq=1000899162&print=print",
        source_as_of,
        2026,
    )
    nts_derivative_guide_2026 = _source(
        "kr-nts-derivative-guide-2026",
        "National Tax Service derivative loss-netting and filing guide",
        "https://nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350890",
        source_as_of,
        2026,
    )
    securities_tax_2025 = _source(
        "kr-securities-transaction-tax-decree-2025",
        "Securities Transaction Tax Act Enforcement Decree Article 5 rate schedule",
        "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=268145",
        source_as_of,
        2025,
    )
    securities_tax_2026 = _source(
        "kr-securities-transaction-tax-decree-2026",
        "2026 Securities Transaction Tax Act Enforcement Decree rate schedule",
        "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285479",
        source_as_of,
        2026,
    )
    securities_tax_form_2026 = _source(
        "kr-securities-transaction-tax-form-2026",
        "2026 securities transaction tax form rates",
        "https://www.law.go.kr/LSW/lsBylInfoP.do?bylSeq=16344404&lsiSeq=285479",
        source_as_of,
        2026,
    )
    virtual_asset_2026 = _source(
        "kr-nts-virtual-asset-2026",
        "Resident virtual-asset income taxation overview before commencement",
        "https://nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=238935&mi=40370",
        source_as_of,
        2026,
    )
    virtual_asset_2027 = _source(
        "kr-nts-virtual-asset-2027",
        "Resident virtual-asset income taxation overview from 2027-01-01",
        "https://nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=238935&mi=40370",
        source_as_of,
        2027,
    )

    rules: list[TaxRule] = []

    foreign_sources = (
        stock_scope_2026,
        stock_deduction_2026,
        stock_rate_2026,
        stock_local_rate_2026,
        nts_stock_guide_2026,
    )
    rules.append(
        _verified_annual_rule(
            rule_id="kr-individual-foreign-listed-equity-non-sme-2026",
            version="kr-stock-income-2026-v1",
            year=2026,
            asset=TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
            sources=foreign_sources,
            loss_netting_pool="KR_STOCK_CAPITAL_GAINS_POOL_2026",
            cost_basis_method=CostBasisMethod.UNSPECIFIED,
            deduction=Decimal("2500000"),
            national_rate=Decimal("0.20"),
            local_rate=Decimal("0.02"),
            input_basis=(
                "calendar-year net capital gain for qualifying foreign listed equities, "
                "after recognized acquisition/disposal costs and before the Article 103 deduction"
            ),
        )
    )
    rules.append(
        _review_rule(
            rule_id="kr-individual-foreign-listed-equity-sme-unknown-2026",
            version="kr-stock-income-2026-v1",
            year=2026,
            asset=TaxAssetClassification.FOREIGN_LISTED_EQUITY_SME_OR_UNKNOWN,
            sources=foreign_sources,
            loss_netting_pool="KR_STOCK_CAPITAL_GAINS_POOL_2026",
            cost_basis_method=CostBasisMethod.UNSPECIFIED,
            reasons=(
                "the 10% versus 20% national rate depends on statutory SME classification",
                "issuer classification cannot be inferred from a ticker or venue",
            ),
        )
    )
    rules.append(
        _review_rule(
            rule_id="kr-individual-domestic-listed-equity-income-2026",
            version="kr-domestic-stock-income-2026-v1",
            year=2026,
            asset=TaxAssetClassification.DOMESTIC_LISTED_EQUITY,
            sources=(stock_scope_2026, stock_deduction_2026, nts_stock_guide_2026),
            loss_netting_pool="KR_STOCK_CAPITAL_GAINS_POOL_2026",
            cost_basis_method=CostBasisMethod.UNSPECIFIED,
            reasons=(
                "capital-gains taxability depends on shareholder and instrument facts outside this key",
                "major-shareholder and special-product conditions require external evidence",
            ),
        )
    )
    derivative_sources = (
        stock_deduction_2026,
        derivative_law_2026,
        derivative_local_2026,
        nts_derivative_guide_2026,
    )
    rules.append(
        _verified_annual_rule(
            rule_id="kr-individual-taxable-derivative-2026",
            version="kr-derivative-income-2026-v1",
            year=2026,
            asset=TaxAssetClassification.DERIVATIVE,
            sources=derivative_sources,
            loss_netting_pool="KR_DERIVATIVE_CAPITAL_GAINS_POOL_2026",
            cost_basis_method=CostBasisMethod.NOT_APPLICABLE,
            deduction=Decimal("2500000"),
            national_rate=Decimal("0.10"),
            local_rate=Decimal("0.01"),
            input_basis=(
                "calendar-year net gain across qualifying domestic and foreign derivatives, "
                "before the separate derivative-pool basic deduction"
            ),
        )
    )
    rules.append(
        _review_rule(
            rule_id="kr-individual-virtual-asset-spot-2026",
            version="kr-virtual-asset-2026-v1",
            year=2026,
            asset=TaxAssetClassification.VIRTUAL_ASSET_SPOT,
            sources=(virtual_asset_2026,),
            loss_netting_pool="REVIEW_REQUIRED",
            cost_basis_method=CostBasisMethod.UNSPECIFIED,
            reasons=(
                "the dedicated virtual-asset regime starts for disposals or lending on 2027-01-01",
                "2026 receipts can still require another income or business classification",
            ),
        )
    )
    rules.append(
        _review_rule(
            rule_id="kr-individual-virtual-asset-spot-2027",
            version="kr-virtual-asset-2027-v1",
            year=2027,
            asset=TaxAssetClassification.VIRTUAL_ASSET_SPOT,
            sources=(virtual_asset_2027,),
            loss_netting_pool="KR_VIRTUAL_ASSET_INCOME_POOL_2027",
            cost_basis_method=CostBasisMethod.UNSPECIFIED,
            reasons=(
                "the future national regime is versioned separately and must not overwrite 2026",
                "local tax, acquisition-cost evidence, account aggregation, and event classification require review",
            ),
        )
    )
    rules.append(
        _review_rule(
            rule_id="kr-individual-crypto-derivative-2026",
            version="kr-crypto-derivative-2026-v1",
            year=2026,
            asset=TaxAssetClassification.CRYPTO_DERIVATIVE,
            sources=(derivative_law_2026, derivative_local_2026, virtual_asset_2026),
            loss_netting_pool="REVIEW_REQUIRED",
            cost_basis_method=CostBasisMethod.NOT_APPLICABLE,
            reasons=(
                "the offshore crypto derivative must first be mapped to an applicable statutory product",
                "funding, basis PnL, and spot-leg events cannot be collapsed into one tax rate",
            ),
        )
    )
    for account_type in (TaxAccountType.ISA, TaxAccountType.PENSION):
        rules.append(
            _review_rule(
                rule_id=f"kr-individual-{account_type.value.lower()}-foreign-equity-2026",
                version=f"kr-{account_type.value.lower()}-2026-v1",
                year=2026,
                asset=TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
                sources=foreign_sources,
                account_type=account_type,
                loss_netting_pool="REVIEW_REQUIRED",
                cost_basis_method=CostBasisMethod.UNSPECIFIED,
                reasons=(
                    "tax-advantaged account eligibility and withdrawal treatment require account evidence",
                ),
            )
        )

    levy_sources_2025 = (securities_tax_2025,)
    levy_sources_2026 = (securities_tax_2026, securities_tax_form_2026)
    levy_rates: tuple[tuple[int, str, Decimal, tuple[TaxRuleSource, ...]], ...] = (
        (2025, "KOSPI", Decimal("0"), levy_sources_2025),
        (2025, "KOSDAQ", Decimal("0.0015"), levy_sources_2025),
        (2026, "KOSPI", Decimal("0.0005"), levy_sources_2026),
        (2026, "KOSDAQ", Decimal("0.0020"), levy_sources_2026),
        (2026, "KONEX", Decimal("0.0010"), levy_sources_2026),
        (2026, "KOTC", Decimal("0.0020"), levy_sources_2026),
    )
    for year, venue, rate, sources in levy_rates:
        rules.append(
            _verified_levy_rule(
                rule_id=f"kr-individual-{venue.lower()}-securities-transaction-tax-{year}",
                version=f"kr-securities-transaction-tax-{year}-v1",
                year=year,
                venue=venue,
                rate=rate,
                sources=sources,
            )
        )

    return TaxRuleRegistry(
        registry_id="kr-individual-tax-rule-registry",
        version="kr-tax-registry-v0.2.0",
        source_as_of=source_as_of,
        rules=tuple(rules),
    )


def _verified_annual_rule(
    *,
    rule_id: str,
    version: str,
    year: int,
    asset: TaxAssetClassification,
    sources: tuple[TaxRuleSource, ...],
    loss_netting_pool: str,
    cost_basis_method: CostBasisMethod,
    deduction: Decimal,
    national_rate: Decimal,
    local_rate: Decimal,
    input_basis: str,
    account_type: TaxAccountType = TaxAccountType.GENERAL_TAXABLE,
) -> TaxRule:
    key = TaxRuleKey("KR", "KR_INDIVIDUAL", year, account_type, asset)
    profile = _profile(
        profile_id=rule_id,
        key=key,
        version=version,
        sources=sources,
        confidence=TaxConfidence.CONFIRMED,
        loss_netting_pool=loss_netting_pool,
        cost_basis_method=cost_basis_method,
    )
    return TaxRule(
        rule_id=rule_id,
        version=version,
        key=key,
        status=TaxRuleResolutionStatus.VERIFIED,
        profile=profile,
        sources=sources,
        computation=TaxComputationRule(
            currency="KRW",
            annual_basic_deduction=deduction,
            national_rate=national_rate,
            local_rate=local_rate,
            input_basis=input_basis,
        ),
    )


def _review_rule(
    *,
    rule_id: str,
    version: str,
    year: int,
    asset: TaxAssetClassification,
    sources: tuple[TaxRuleSource, ...],
    loss_netting_pool: str,
    cost_basis_method: CostBasisMethod,
    reasons: tuple[str, ...],
    account_type: TaxAccountType = TaxAccountType.GENERAL_TAXABLE,
) -> TaxRule:
    key = TaxRuleKey("KR", "KR_INDIVIDUAL", year, account_type, asset)
    profile = _profile(
        profile_id=rule_id,
        key=key,
        version=version,
        sources=sources,
        confidence=TaxConfidence.REVIEW_REQUIRED,
        loss_netting_pool=loss_netting_pool,
        cost_basis_method=cost_basis_method,
    )
    return TaxRule(
        rule_id=rule_id,
        version=version,
        key=key,
        status=TaxRuleResolutionStatus.REVIEW_REQUIRED,
        profile=profile,
        sources=sources,
        review_reasons=reasons,
    )


def _verified_levy_rule(
    *,
    rule_id: str,
    version: str,
    year: int,
    venue: str,
    rate: Decimal,
    sources: tuple[TaxRuleSource, ...],
) -> TaxRule:
    key = TaxRuleKey(
        jurisdiction="KR",
        residency="KR_INDIVIDUAL",
        tax_year=year,
        account_type=TaxAccountType.GENERAL_TAXABLE,
        asset_classification=TaxAssetClassification.DOMESTIC_LISTED_EQUITY,
        purpose=TaxRulePurpose.TRANSACTION_LEVY,
        venue=venue,
    )
    profile = _profile(
        profile_id=rule_id,
        key=key,
        version=version,
        sources=sources,
        confidence=TaxConfidence.CONFIRMED,
        loss_netting_pool="NOT_APPLICABLE_TRANSACTION_LEVY",
        cost_basis_method=CostBasisMethod.NOT_APPLICABLE,
    )
    return TaxRule(
        rule_id=rule_id,
        version=version,
        key=key,
        status=TaxRuleResolutionStatus.VERIFIED,
        profile=profile,
        sources=sources,
        computation=TransactionLevyComputationRule(
            currency="KRW",
            rate_components=(
                TaxRateComponent(
                    component_id="securities-transaction-tax",
                    kind=TaxRateComponentKind.SECURITIES_TRANSACTION_TAX,
                    rate=rate,
                ),
            ),
            input_basis=(
                "gross sale proceeds for the securities transaction tax component only; "
                "other levies are not silently included"
            ),
        ),
    )


def _profile(
    *,
    profile_id: str,
    key: TaxRuleKey,
    version: str,
    sources: tuple[TaxRuleSource, ...],
    confidence: TaxConfidence,
    loss_netting_pool: str,
    cost_basis_method: CostBasisMethod,
) -> TaxProfile:
    year_start = date(key.tax_year, 1, 1)
    year_end = date(key.tax_year, 12, 31)
    return TaxProfile(
        profile_id=profile_id,
        jurisdiction=key.jurisdiction,
        residency=key.residency,
        tax_year=key.tax_year,
        account_type=key.account_type.value,
        asset_classification=key.asset_classification.value,
        cost_basis_method=cost_basis_method,
        loss_netting_pool=loss_netting_pool,
        base_currency="KRW",
        rule_version=version,
        effective_from=year_start,
        effective_to=year_end,
        source_reference=";".join(source.reference_url for source in sources),
        confidence=confidence,
    )


def _source(
    source_id: str,
    title: str,
    reference_url: str,
    source_as_of: date,
    year: int,
) -> TaxRuleSource:
    return TaxRuleSource(
        source_id=source_id,
        authority=(
            "National Tax Service of Korea"
            if "nts" in source_id
            else "Korea Ministry of Government Legislation"
        ),
        title=title,
        reference_url=reference_url,
        source_as_of=source_as_of,
        effective_from=date(year, 1, 1),
        effective_to=date(year, 12, 31),
    )


def _intervals_overlap(first: TaxRule, second: TaxRule) -> bool:
    first_end = first.profile.effective_to or date.max
    second_end = second.profile.effective_to or date.max
    return first.profile.effective_from <= second_end and second.profile.effective_from <= first_end


def _require_identifiers(values: Sequence[str], name: str) -> tuple[str, ...]:
    identifiers = tuple(values)
    if not identifiers or any(not identifier.strip() for identifier in identifiers):
        raise ValueError(f"{name} must contain non-empty values")
    return identifiers


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_rate(value: Decimal, name: str) -> None:
    _require_finite(value, name)
    if value < ZERO or value > ONE:
        raise ValueError(f"{name} must be between 0 and 1")


def _require_finite(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")


def _require_non_negative(value: Decimal, name: str) -> None:
    _require_finite(value, name)
    if value < ZERO:
        raise ValueError(f"{name} must be non-negative")
