"""Versioned tax-rule registry contracts and the Korean resident v0 registry."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .finance import CostBasisMethod, TaxConfidence, TaxEstimate, TaxProfile

ZERO = Decimal("0")
ONE = Decimal("1")


class TaxRuleResolutionStatus(StrEnum):
    VERIFIED = "verified"
    REVIEW_REQUIRED = "review_required"
    MISSING = "missing"


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


@dataclass(frozen=True, slots=True)
class TaxRuleKey:
    jurisdiction: str
    residency: str
    tax_year: int
    account_type: TaxAccountType
    asset_classification: TaxAssetClassification

    def __post_init__(self) -> None:
        _require_text(self.jurisdiction, "jurisdiction")
        _require_text(self.residency, "residency")
        if self.tax_year < 1900:
            raise ValueError("tax_year must be at least 1900")


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
        return self.effective_from <= period_start and (
            self.effective_to is None or self.effective_to >= period_end
        )


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
        if self.annual_basic_deduction < ZERO:
            raise ValueError("annual_basic_deduction must be non-negative")
        for name in ("national_rate", "local_rate"):
            rate = getattr(self, name)
            if not rate.is_finite() or rate < ZERO or rate > ONE:
                raise ValueError(f"{name} must be a finite decimal between 0 and 1")

    @property
    def combined_rate(self) -> Decimal:
        return self.national_rate + self.local_rate

    def estimate(
        self,
        *,
        annual_net_gain: Decimal,
        estimate_id: str,
        profile: TaxProfile,
        source_event_ids: Sequence[str],
    ) -> TaxEstimate:
        _require_text(estimate_id, "estimate_id")
        if not annual_net_gain.is_finite():
            raise ValueError("annual_net_gain must be finite")
        identifiers = tuple(source_event_ids)
        if not identifiers or any(not identifier.strip() for identifier in identifiers):
            raise ValueError("source_event_ids must contain non-empty values")
        taxable_base = max(ZERO, annual_net_gain - self.annual_basic_deduction)
        return TaxEstimate(
            estimate_id=estimate_id,
            profile_id=profile.profile_id,
            tax_year=profile.tax_year,
            currency=self.currency,
            taxable_base=taxable_base,
            estimated_tax=taxable_base * self.combined_rate,
            rule_version=profile.rule_version,
            confidence=TaxConfidence.CONFIRMED,
            source_event_ids=identifiers,
        )


@dataclass(frozen=True, slots=True)
class TaxRule:
    rule_id: str
    version: str
    key: TaxRuleKey
    status: TaxRuleResolutionStatus
    profile: TaxProfile
    sources: tuple[TaxRuleSource, ...]
    computation: TaxComputationRule | None = None
    review_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.rule_id, "rule_id")
        _require_text(self.version, "version")
        if not self.sources:
            raise ValueError("sources must not be empty")
        if any(not source.applies_to(self.key.tax_year) for source in self.sources):
            raise ValueError("every source must cover the complete tax year")
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
        if self.status is TaxRuleResolutionStatus.VERIFIED:
            if self.computation is None:
                raise ValueError("verified rules require a computation")
            if self.review_reasons:
                raise ValueError("verified rules must not contain review reasons")
            if self.profile.confidence is not TaxConfidence.CONFIRMED:
                raise ValueError("verified profiles must use confirmed confidence")
        elif self.status is TaxRuleResolutionStatus.REVIEW_REQUIRED:
            if self.computation is not None:
                raise ValueError("review-required rules must not expose a computation")
            if not self.review_reasons or any(not value.strip() for value in self.review_reasons):
                raise ValueError("review-required rules need explicit reasons")
            if self.profile.confidence is not TaxConfidence.REVIEW_REQUIRED:
                raise ValueError("review-required profiles must use review_required confidence")
        else:
            raise ValueError("stored rules must be verified or review_required")

    def estimate(
        self,
        *,
        annual_net_gain: Decimal,
        estimate_id: str,
        source_event_ids: Sequence[str],
    ) -> TaxEstimate:
        if self.status is not TaxRuleResolutionStatus.VERIFIED or self.computation is None:
            raise ValueError("tax estimate is unavailable until this rule is verified")
        return self.computation.estimate(
            annual_net_gain=annual_net_gain,
            estimate_id=estimate_id,
            profile=self.profile,
            source_event_ids=source_event_ids,
        )


@dataclass(frozen=True, slots=True)
class TaxRuleResolution:
    status: TaxRuleResolutionStatus
    key: TaxRuleKey
    rule: TaxRule | None
    message: str

    def __post_init__(self) -> None:
        _require_text(self.message, "message")
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
        keys = tuple(rule.key for rule in self.rules)
        if len(keys) != len(set(keys)):
            raise ValueError("tax rule keys must be unique")
        if any(rule.version != self.version for rule in self.rules):
            raise ValueError("all rule versions must match the registry version")
        if any(
            source.source_as_of > self.source_as_of
            for rule in self.rules
            for source in rule.sources
        ):
            raise ValueError("registry source_as_of must cover every source")

    def resolve(self, key: TaxRuleKey) -> TaxRuleResolution:
        for rule in self.rules:
            if rule.key == key:
                if rule.status is TaxRuleResolutionStatus.VERIFIED:
                    message = "verified rule resolved; automatic estimate is available"
                else:
                    message = "expert review is required before any automatic estimate"
                return TaxRuleResolution(rule.status, key, rule, message)
        return TaxRuleResolution(
            TaxRuleResolutionStatus.MISSING,
            key,
            None,
            "no rule is registered for this exact residence/year/account/product key",
        )


def build_kr_individual_tax_registry_v0() -> TaxRuleRegistry:
    version = "kr-tax-v0.1.0"
    source_as_of = date(2026, 7, 18)
    year_start = date(2026, 1, 1)
    year_end = date(2026, 12, 31)

    income_scope = TaxRuleSource(
        source_id="kr-income-tax-act-94-2026",
        authority="Korea Ministry of Government Legislation",
        title="Income Tax Act Article 94 and Enforcement Decree Article 157-3",
        reference_url="https://www.law.go.kr/LSW/LsiJoLinkP.do?docType=JO&joNo=002000003&languageType=KO&lsNm=%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95&paras=1",
        source_as_of=source_as_of,
        effective_from=year_start,
        effective_to=year_end,
    )
    income_deduction = TaxRuleSource(
        source_id="kr-income-tax-act-103-2026",
        authority="Korea Ministry of Government Legislation",
        title="Income Tax Act Article 103 annual capital-gains basic deduction",
        reference_url="https://law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000383716",
        source_as_of=source_as_of,
        effective_from=year_start,
        effective_to=year_end,
    )
    income_rate = TaxRuleSource(
        source_id="kr-income-tax-act-104-2026",
        authority="Korea Ministry of Government Legislation",
        title="Income Tax Act Article 104(1)(12) foreign-share rates",
        reference_url="https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001565&lsJoLnkSeq=1000820745",
        source_as_of=source_as_of,
        effective_from=year_start,
        effective_to=year_end,
    )
    local_rate = TaxRuleSource(
        source_id="kr-local-tax-act-103-3-2026",
        authority="Korea Ministry of Government Legislation",
        title="Local Tax Act Article 103-3(1)(12) foreign-share local-income-tax rates",
        reference_url="https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001649&lsJoLnkSeq=1000899162&print=print",
        source_as_of=source_as_of,
        effective_from=year_start,
        effective_to=year_end,
    )
    virtual_asset_source = TaxRuleSource(
        source_id="kr-nts-virtual-asset-2026",
        authority="National Tax Service of Korea",
        title="Resident virtual-asset income taxation overview",
        reference_url="https://nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=238935&mi=40370",
        source_as_of=source_as_of,
        effective_from=year_start,
        effective_to=year_end,
    )
    common_sources = (income_scope, income_deduction, income_rate, local_rate)

    def profile(
        *,
        profile_id: str,
        account_type: TaxAccountType,
        asset: TaxAssetClassification,
        confidence: TaxConfidence,
        sources: tuple[TaxRuleSource, ...],
    ) -> TaxProfile:
        return TaxProfile(
            profile_id=profile_id,
            jurisdiction="KR",
            residency="KR_INDIVIDUAL",
            tax_year=2026,
            account_type=account_type.value,
            asset_classification=asset.value,
            cost_basis_method=CostBasisMethod.UNSPECIFIED,
            loss_netting_pool=(
                "KR_STOCK_CAPITAL_GAINS_POOL_2026"
                if asset
                in {
                    TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
                    TaxAssetClassification.FOREIGN_LISTED_EQUITY_SME_OR_UNKNOWN,
                    TaxAssetClassification.DOMESTIC_LISTED_EQUITY,
                }
                else "REVIEW_REQUIRED"
            ),
            base_currency="KRW",
            rule_version=version,
            effective_from=year_start,
            effective_to=year_end,
            source_reference=";".join(source.reference_url for source in sources),
            confidence=confidence,
        )

    verified_key = TaxRuleKey(
        jurisdiction="KR",
        residency="KR_INDIVIDUAL",
        tax_year=2026,
        account_type=TaxAccountType.GENERAL_TAXABLE,
        asset_classification=TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
    )
    rules: list[TaxRule] = [
        TaxRule(
            rule_id="kr-individual-foreign-listed-equity-non-sme-2026",
            version=version,
            key=verified_key,
            status=TaxRuleResolutionStatus.VERIFIED,
            profile=profile(
                profile_id="kr-individual-general-foreign-listed-equity-non-sme-2026",
                account_type=verified_key.account_type,
                asset=verified_key.asset_classification,
                confidence=TaxConfidence.CONFIRMED,
                sources=common_sources,
            ),
            sources=common_sources,
            computation=TaxComputationRule(
                currency="KRW",
                annual_basic_deduction=Decimal("2500000"),
                national_rate=Decimal("0.20"),
                local_rate=Decimal("0.02"),
                input_basis=(
                    "calendar-year net capital gain for qualifying foreign listed equities, "
                    "after acquisition/disposal costs and before the Article 103 basic deduction"
                ),
            ),
        )
    ]

    review_specs = (
        (
            TaxAccountType.GENERAL_TAXABLE,
            TaxAssetClassification.FOREIGN_LISTED_EQUITY_SME_OR_UNKNOWN,
            common_sources,
            (
                "the 10% versus 20% national rate depends on the statutory SME classification",
                "the registry cannot infer issuer classification from a ticker or venue",
            ),
        ),
        (
            TaxAccountType.GENERAL_TAXABLE,
            TaxAssetClassification.DOMESTIC_LISTED_EQUITY,
            common_sources,
            (
                "taxability depends on shareholder and instrument conditions not present in this key",
                "major-shareholder and special-product facts require an external evidence check",
            ),
        ),
        (
            TaxAccountType.GENERAL_TAXABLE,
            TaxAssetClassification.DERIVATIVE,
            common_sources,
            (
                "product inclusion and tax-base rules require the exact contract classification",
            ),
        ),
        (
            TaxAccountType.GENERAL_TAXABLE,
            TaxAssetClassification.VIRTUAL_ASSET_SPOT,
            (virtual_asset_source,),
            (
                "the virtual-asset income regime starts for disposals or lending on or after 2027-01-01",
                "2026 transactions can still require review for other income or instrument classifications",
            ),
        ),
        (
            TaxAccountType.GENERAL_TAXABLE,
            TaxAssetClassification.CRYPTO_DERIVATIVE,
            (income_rate, local_rate, virtual_asset_source),
            (
                "an offshore crypto derivative must be classified under the applicable derivative rules",
                "funding, basis PnL, and spot-leg events cannot be collapsed into one tax rate",
            ),
        ),
        (
            TaxAccountType.ISA,
            TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
            common_sources,
            ("tax-advantaged account eligibility and treatment require account-specific review",),
        ),
        (
            TaxAccountType.PENSION,
            TaxAssetClassification.FOREIGN_LISTED_EQUITY_NON_SME,
            common_sources,
            ("pension-account deferral and withdrawal treatment require account-specific review",),
        ),
    )
    for account_type, asset, sources, reasons in review_specs:
        key = TaxRuleKey("KR", "KR_INDIVIDUAL", 2026, account_type, asset)
        rules.append(
            TaxRule(
                rule_id=f"kr-individual-{account_type.value.lower()}-{asset.value.lower()}-2026",
                version=version,
                key=key,
                status=TaxRuleResolutionStatus.REVIEW_REQUIRED,
                profile=profile(
                    profile_id=f"kr-individual-{account_type.value.lower()}-{asset.value.lower()}-2026",
                    account_type=account_type,
                    asset=asset,
                    confidence=TaxConfidence.REVIEW_REQUIRED,
                    sources=sources,
                ),
                sources=sources,
                review_reasons=reasons,
            )
        )

    return TaxRuleRegistry(
        registry_id="kr-individual-tax-rule-registry",
        version=version,
        source_as_of=source_as_of,
        rules=tuple(rules),
    )


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
