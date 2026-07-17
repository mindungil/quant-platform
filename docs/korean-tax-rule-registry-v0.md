# Korean individual tax rule registry v0

## Scope

`build_kr_individual_tax_registry_v0()` is a research-estimation registry, not a tax filing engine or legal opinion. Its source snapshot is **2026-07-18**, and every v0 rule is limited to tax year 2026.

The registry resolves an exact tuple:

```text
jurisdiction + residency + tax year + account type + asset classification
```

It has three outcomes:

- `verified`: the exact key has a computation rule.
- `review_required`: an official source exists, but facts outside the key are needed before computing tax.
- `missing`: no exact rule exists. The caller must not fall back to another product or jurisdiction.

## Only automatic v0 calculation

The sole `verified` calculation is deliberately narrow:

- Korean resident individual
- 2026 tax year
- ordinary taxable account
- foreign listed equity already confirmed **not** to be in the statutory SME rate branch
- input supplied as calendar-year net capital gain in KRW after acquisition/disposal costs, but before the annual capital-gains basic deduction

For that exact input, v0 applies:

```text
taxable base = max(0, annual net gain - KRW 2,500,000)
estimated tax = taxable base × (20% national + 2% local)
```

The registry does not derive acquisition cost, convert foreign currencies, classify an issuer as an SME, or prepare a return. Those steps must be completed upstream from broker records and reviewed separately.

## Explicit review-required cases

The following keys never expose a calculation in v0:

- foreign listed equity whose SME classification is unknown
- domestic listed equity
- derivatives without an exact statutory product classification
- virtual-asset spot transactions
- crypto derivatives and funding-carry structures
- ISA and pension accounts

A `review_required` rule contains reasons and source evidence but has `computation=None`. Calling `estimate()` raises instead of returning an assumed number.

The virtual-asset review boundary is intentional. The National Tax Service states that the dedicated virtual-asset income regime applies to disposals or lending from **2027-01-01**. That does not prove that every 2026 crypto-related cash flow has no Korean tax consequence: staking, lending, derivatives, business activity, and mixed spot/derivative structures can require different classification.

## Official source snapshot

- Income Tax Act Article 94 and Enforcement Decree Article 157-3: foreign-share scope
  - https://www.law.go.kr/LSW/LsiJoLinkP.do?docType=JO&joNo=002000003&languageType=KO&lsNm=%EC%86%8C%EB%93%9D%EC%84%B8%EB%B2%95&paras=1
- Income Tax Act Article 103: annual KRW 2.5 million capital-gains basic deduction by statutory income group
  - https://law.go.kr/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000383716
- Income Tax Act Article 104(1)(12): 10%/20% national rates for foreign shares depending on the statutory SME branch
  - https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001565&lsJoLnkSeq=1000820745
- Local Tax Act Article 103-3(1)(12): corresponding 1%/2% local-income-tax rates
  - https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsId=001649&lsJoLnkSeq=1000899162&print=print
- National Tax Service virtual-asset income overview: dedicated regime starts with disposals/lending on or after 2027-01-01
  - https://nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=238935&mi=40370
- National Tax Service 2026 filing notice: foreign-share capital gains are included in the annual capital-gains filing population
  - https://nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2201&nttSn=1350890

## Expert-review checklist before Paper or Live

A tax professional or qualified reviewer must confirm and record all applicable items:

1. Tax residence and taxpayer type for the complete tax year.
2. Account legal type: ordinary taxable, ISA, pension, corporate, trust, or another wrapper.
3. Exact legal classification of every product and cash flow, including ETF, ETN, fund, ADR, derivative, token, staking, lending, airdrop, rebate, and funding payment.
4. Whether a foreign issuer belongs to the statutory SME branch.
5. Whether domestic-share major-shareholder or special-product conditions apply.
6. Acquisition-cost method, broker adjustments, commissions, taxes, and recognized deductible expenses.
7. KRW conversion source and transaction-date convention for acquisition, disposal, income, and expenses.
8. Calendar-year loss netting and whether another account or broker shares the same statutory pool and basic deduction.
9. Foreign tax paid, withholding, treaty relief, and foreign-tax-credit treatment.
10. Account transfers, gifts, inheritance, corporate actions, stock splits, mergers, and missing cost basis.
11. Filing deadline, supporting evidence, and reconciliation against broker and bank statements.
12. Whether law, decree, administrative guidance, or the taxpayer's facts changed after `source_as_of`.

The reviewed decision must pin the registry version, source snapshot, reviewer, reviewed facts, unresolved assumptions, and the final profile used by the experiment manifest.
