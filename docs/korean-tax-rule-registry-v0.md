# Korean individual tax rule registry v0

## Scope and safety boundary

`build_kr_individual_tax_registry_v0()` is a versioned research-estimation registry. It is not a tax filing engine, a final tax assessment, or legal advice. Its official-source snapshot is **2026-07-18**.

Every query resolves the exact tuple below for a concrete date:

```text
jurisdiction
+ residency
+ transaction date / tax year
+ account type
+ asset classification
+ purpose (annual income tax or transaction levy)
+ venue
```

The outcomes are deliberately closed:

- `verified`: the exact key and date have a computation rule.
- `review_required`: evidence exists, but additional taxpayer or product facts are required.
- `missing`: no exact rule exists. There is no fallback to another year, product, purpose, venue, account, or jurisdiction.

Every returned amount has `finality=estimate_only`. The API does not expose a “final tax” state.

## Version and effective-date selection

`TaxRuleQuery` derives `tax_year` from the supplied date. `TaxRuleRegistry.resolve_for_date()` then selects one non-overlapping effective rule. The registry rejects overlapping intervals for the same exact key.

This prevents two common errors:

1. applying the current rule to a historical transaction;
2. allowing a future rule to overwrite the current research result.

The tested boundaries include:

- KOSPI securities transaction tax: 2025-12-31 versus 2026-01-01 and 2026-01-02;
- virtual-asset spot: 2026-12-31 versus 2027-01-01 and 2027-01-02.

## Verified automatic calculations

### 1. Foreign listed equity, confirmed non-SME branch, 2026

Scope:

- Korean-resident individual;
- ordinary taxable account;
- qualifying foreign listed equity already confirmed outside the statutory SME branch;
- input is calendar-year KRW net capital gain after recognized acquisition/disposal costs and before the annual basic deduction.

```text
taxable base = max(0, annual net gain - KRW 2,500,000)
national component = taxable base × 20%
local component = taxable base × 2%
estimated total = national + local
```

The registry does not infer issuer SME status, derive acquisition cost, or convert foreign currency.

### 2. Qualifying domestic and foreign derivatives, 2026

Scope:

- product has already been confirmed as a derivative included in the statutory capital-gains regime;
- input is the annual net gain across the separate derivative loss-netting pool.

```text
taxable base = max(0, annual derivative net gain - KRW 2,500,000)
national component = taxable base × 10%
local component = taxable base × 1%
estimated total = national + local
```

Crypto perpetuals are not automatically mapped to this rule.

### 3. Domestic-market securities transaction tax component

The transaction-levy purpose is separate from annual income tax. The verified component rates are:

| Effective year | Venue | Securities transaction tax component |
| --- | --- | ---: |
| 2025 | KOSPI | 0% |
| 2025 | KOSDAQ | 0.15% |
| 2026 | KOSPI | 0.05% |
| 2026 | KOSDAQ | 0.20% |
| 2026 | KONEX | 0.10% |
| 2026 | K-OTC | 0.20% |

The input is gross sale proceeds. This rule models only the named securities transaction tax component. It does not silently add another levy or claim an all-in burden.

`transaction_levy_ledger_entry()` records the estimate as a negative `TRANSACTION_TAX` financial-ledger entry. Annual income-tax estimates remain outside the execution-cost ledger, while actual withholding and payments use the separate tax cash-flow entry kinds.

## Review-required rules

The following paths have official evidence but no automatic computation:

- foreign listed equity with unknown statutory SME classification;
- domestic listed-equity capital gains, because shareholder and instrument conditions are missing;
- 2026 virtual-asset spot transactions, because the dedicated regime has not started and another classification may apply;
- the separately versioned 2027 virtual-asset spot regime, until local tax, cost-basis evidence, account aggregation, and exact event classification are reviewed;
- offshore crypto derivatives, funding payments, and mixed funding-carry structures;
- ISA and pension accounts.

A `review_required` rule has `computation=None`. Any attempt to calculate raises instead of returning an assumed number.

## Hand-calculation verification

The automated tests reproduce these manual calculations:

| Case | Manual result |
| --- | ---: |
| KOSPI 2026 sale proceeds KRW 10,000,000 | transaction tax component KRW 5,000 |
| KOSDAQ 2026 sale proceeds KRW 10,000,000 | transaction tax component KRW 20,000 |
| Foreign equity annual net gain KRW 10,000,000 | base 7,500,000; national 1,500,000; local 150,000; total 1,650,000 |
| Derivative annual net gain KRW 130,000,000 | base 127,500,000; national 12,750,000; local 1,275,000; total 14,025,000 |
| KOSPI KRW 10,000,000 on 2025-12-31 / 2026-01-01 | KRW 0 / KRW 5,000 |
| Virtual spot on 2026-12-31 / 2027-01-01 | different rule versions; both remain review-required |
| Crypto perpetual | review-required; no computation object |

Tests also cover exact-purpose non-fallback, wrong-year rejection, overlapping-rule rejection, zero tax below the deduction, and transaction-levy ledger conversion.

## Explicit profile fields

Every profile records:

- jurisdiction and residence;
- tax year and effective interval;
- account and asset classification;
- cost-basis method, including `UNSPECIFIED` or `NOT_APPLICABLE` when the registry must not invent one;
- loss-netting pool;
- base currency;
- rule version;
- source references;
- confidence.

Using `UNSPECIFIED` is an explicit block on automatic cost-basis derivation, not an invitation to guess.

## Public / Private boundary decision

The initial hypothesis placed concrete Korean rule data in `quant-alpha`. The reviewed boundary is different:

- **Public `quant-platform`** owns non-proprietary, auditable jurisdiction rules, source metadata, date selection, calculation contracts, and examples.
- **Private `quant-alpha`** owns proprietary strategy-leg mappings, account context, unresolved product classification, and promotion decisions.
- **Private `quant-ops`** pins validated Public and Private revisions for deployment.

This keeps legal assumptions inspectable without exposing alpha logic or operational secrets.

## Official source snapshot

- Income Tax Act Article 94 and Enforcement Decree Article 157-3: stock capital-gains scope
  - https://www.law.go.kr/LSW/LsiJoLinkP.do?docType=JO&joNo=002000003&languageType=KO&lsNm=%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95&paras=1
- Income Tax Act Article 103: annual KRW 2.5 million capital-gains basic deduction
  - https://law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000383716
- Income Tax Act Article 104: foreign-share and derivative national rates
  - https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001565&lsJoLnkSeq=1000820745
- Local Tax Act and Enforcement Decree: corresponding local-income-tax components
  - https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001649&lsJoLnkSeq=1000899162&print=print
- Securities Transaction Tax Act Enforcement Decree and 2026 form
  - https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=285479
  - https://www.law.go.kr/LSW/lsBylInfoP.do?bylSeq=16344404&lsiSeq=285479
- National Tax Service 2026 filing guide: stock and derivative netting examples
  - https://nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350890
- National Tax Service virtual-asset overview: dedicated regime from 2027-01-01
  - https://nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=238935&mi=40370

## Expert-review checklist before Paper or Live

A qualified reviewer must confirm and record all applicable items:

1. Tax residence and taxpayer type for the complete year.
2. Legal account type.
3. Exact legal classification of every instrument and cash flow.
4. Foreign issuer SME classification.
5. Domestic-share shareholder and special-product conditions.
6. Acquisition-cost method and recognized expenses.
7. KRW conversion source and transaction-date convention.
8. Cross-account and cross-broker loss-netting and deduction usage.
9. Foreign tax, withholding, treaty, and foreign-tax-credit treatment.
10. Transfers, gifts, inheritance, and corporate actions.
11. Filing evidence and broker/bank-statement reconciliation.
12. Changes after the registry `source_as_of` date.

The decision package must pin the registry version, Public revision, Private mapping revision, source snapshot, reviewer, reviewed facts, unresolved assumptions, and the profile used by the experiment manifest.
