# Research Workbench

The research workbench separates fast candidate exploration from sealed validation.

## Exploration

`CandidateSpec` stores canonical string parameters so candidate identity is stable across Python runtimes. `ExplorationRunner` evaluates candidates in declared order and records one immutable attempt per candidate.

Each successful evaluation returns:

- named finite metrics,
- named guard results such as point-in-time or look-ahead regression checks,
- optional notes.

Evaluator exceptions are retained as failed attempts instead of silently removing a trial. The resulting `ExplorationReport` serializes deterministically and exposes a content SHA-256.

Candidate choice is explicit. `record_selection()` appends a reasoned decision, and a failed attempt or an attempt with any failed guard cannot be selected. Reversing a prior selection appends a new decision rather than rewriting history.

Exploration results are candidate-discovery evidence. They are not promotion evidence by themselves.

## Validation transition

`ValidationPlan.from_exploration()` accepts only the latest explicitly selected, guard-passing attempt. It freezes:

- the exploration report SHA-256,
- selected attempt ID,
- dataset IDs,
- code revision,
- split-policy SHA-256,
- acceptance-criteria SHA-256,
- Holdout dataset SHA-256.

Changing the candidate, split, acceptance criteria, Holdout data, dataset set, or code revision therefore requires a new plan.

## Promotion boundary

`ValidationResult` contains named validation gates. Promotion eligibility requires at least one gate and every gate to pass. `require_promotion_eligible()` fails closed and reports failed gate IDs.

Strategy-specific parameter generation, metric calculation, data loading, and economic acceptance thresholds remain private adapters. The public package owns deterministic orchestration, evidence, selection history, sealing, and fail-closed validation semantics.
