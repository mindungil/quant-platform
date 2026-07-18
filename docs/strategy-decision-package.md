# Strategy Decision Package and lifecycle gates

A Strategy Decision Package is the immutable evidence bundle used to decide whether a strategy may move from an idea into research validation, Paper, or Live operation. It records what was evaluated, which exact revisions were used, which gate passed or failed, who made the decision, and why.

The package is an audit and reproducibility contract. It does not decide whether a strategy is profitable and does not permit Live trading by itself.

## Lifecycle

```text
IDEA
  -> IMPLEMENTED
  -> DEVELOPMENT_VALIDATED
  -> HOLDOUT_VALIDATED
  -> PAPER
  -> LIVE_CANDIDATE
  -> LIVE
  -> RETIRED
```

Skipping a state is rejected. `RETIRED` is terminal. Every non-retired state may move directly to `RETIRED` only through a passing retirement gate and an approved decision.

| Transition | Required gate |
| --- | --- |
| IDEA -> IMPLEMENTED | IMPLEMENTATION |
| IMPLEMENTED -> DEVELOPMENT_VALIDATED | DEVELOPMENT_VALIDATION |
| DEVELOPMENT_VALIDATED -> HOLDOUT_VALIDATED | HOLDOUT_VALIDATION |
| HOLDOUT_VALIDATED -> PAPER | PAPER_READINESS |
| PAPER -> LIVE_CANDIDATE | PAPER_RECONCILIATION |
| LIVE_CANDIDATE -> LIVE | LIVE_READINESS |
| any active state -> RETIRED | RETIREMENT |

An approved decision must reference the required passing gate. A held or rejected decision keeps the current lifecycle state and must contain at least one blocking reason. The reason schema separates a stable code, severity, human detail, and supporting evidence IDs.

## Immutable revision evidence

`RevisionEvidence` pins the exact inputs that produced a decision:

- `DATASET`: immutable market or event data snapshot
- `CODE`: a 40-character Git commit SHA
- `CONFIG`: strategy or experiment configuration
- `RULE`: tax, fee, execution, acceptance, or risk rule revision
- `ENVIRONMENT`: dependency or runtime environment lock
- `REPORT`: generated validation report
- `ARTIFACT`: packaged strategy or model artifact

A package at `DEVELOPMENT_VALIDATED` or later must contain at least one `DATASET`, `CODE`, and `RULE` revision. Evidence IDs are unique, and every gate result or decision reason must reference evidence already present in the package.

## Holdout seal

The holdout policy is sealed before holdout evaluation. `HoldoutSeal` fixes:

- the dataset evidence ID,
- the last development date,
- the holdout interval,
- the split specification SHA-256,
- the acceptance-criteria SHA-256,
- the seal timestamp.

The seal can be attached only once and cannot be replaced. A `HOLDOUT_VALIDATION` result is accepted only when it occurs after the seal and uses the exact sealed acceptance-criteria digest. This blocks changing the split or success criteria after seeing holdout results.

A materially changed dataset, strategy, rule, or holdout policy must create a new package/version rather than rewrite the existing decision history.

## Decision outputs

`StrategyDecisionPackage.to_json()` produces deterministic, sorted JSON suitable for storage and hashing. `content_sha256()` identifies that JSON representation. `to_markdown()` produces a human-readable report containing revisions, the holdout seal, gate results, decisions, blocking reasons, and known limitations.

Example:

```python
from datetime import UTC, datetime

from quant_platform import (
    DigestAlgorithm,
    RevisionEvidence,
    RevisionKind,
    StrategyDecisionPackage,
    StrategyLifecycleState,
)

now = datetime(2026, 7, 18, tzinfo=UTC)
package = StrategyDecisionPackage(
    package_id="funding-carry-v1",
    strategy_id="funding_carry",
    package_version="1.0.0",
    hypothesis="Funding income can exceed basis and execution costs.",
    state=StrategyLifecycleState.IDEA,
    created_at=now,
    updated_at=now,
    revisions=(
        RevisionEvidence(
            evidence_id="code",
            kind=RevisionKind.CODE,
            name="quant-alpha",
            reference="https://github.com/example/quant-alpha",
            revision="a" * 40,
            digest_algorithm=DigestAlgorithm.GIT_SHA1,
            digest="a" * 40,
        ),
    ),
)
```

Callers add gate results, seal the holdout policy, and apply approval records through the package methods. Directly editing stored JSON or reconstructing a package to bypass these methods is outside the contract and must be rejected by the persistence or operations layer.

## Repository boundary

`quant-platform` owns reusable states, evidence schemas, gates, transition validation, serialization, and holdout immutability rules. `quant-alpha` owns proprietary hypotheses, strategy parameters, private experiment evidence, and concrete decision packages. `quant-ops` later pins approved package hashes and compatible Public/Private revisions for deployment.
