# Quant Platform

Quant Platform is an Apache-2.0 open-core foundation for deterministic quantitative research, backtesting contracts, risk controls, and paper-trading orchestration.

## Repository boundary

This public repository contains reusable contracts, reference implementations, examples, and tests. Proprietary strategies and production operations are maintained separately.

```text
quant-platform  (public)  stable contracts and reusable core
      ↑
quant-alpha     (private) proprietary strategy implementations
      ↑
quant-ops       (private) deployment, environments and runbooks
```

The dependency direction is intentional:

- `quant-platform` runs and tests without either private repository.
- `quant-alpha` depends on released public-core versions and implements its protocols.
- `quant-ops` pins and deploys compatible versions; application code does not import it.
- `quant-platform-internal` is a historical migration reference, not an active development repository.

## Where development happens

Develop reusable contracts, engines, and non-proprietary reference implementations directly in this repository. Use a feature branch, open a pull request, and squash-merge it after CI and boundary review.

Develop proprietary strategies, factors, models, portfolio logic, and private execution extensions in `quant-alpha`. When private work reveals a generally useful abstraction, rewrite only that abstraction as a clean public pull request; never synchronize private directories into this repository.

Use `quant-ops` only to assemble pinned public and private releases, deployment configuration, monitoring, backup, and runbooks.

## Change flow

```text
1. Public contract/core PR
2. Squash merge into public main
3. Tag a compatible public release
4. Update quant-alpha compatibility and tests
5. Pin tested public/private revisions in quant-ops
6. Deploy through the private operations workflow
```

## Current public surface

- immutable market, signal, risk, and order contracts
- an append-only event-sourced order, fill, position, and multi-currency cash engine
- deterministic order lifecycle, spot and derivative settlement, PnL, marks, and replay
- a reference adapter that freezes one strategy target artifact and runs it through vectorized and event-driven execution
- versioned venue constraints, maker/taker matching, latency, volume participation, partial fills, and cancel/replace
- versioned funding schedules, explicit borrow and margin interest, isolated-margin state, and forced liquidation evidence
- execution-reality, signed financial-ledger, tax-lot, and taxable-event contracts
- immutable Strategy Decision Packages, lifecycle promotion gates, and Holdout seals
- deterministic Dataset Registries, Experiment Manifests, and Research Workbench contracts
- an explicit plugin registry
- a deliberately simple non-production alpha example
- strict CI for tests, typing, linting, and public/private boundary checks

Execution state and strategy comparison semantics are documented in [`docs/event-sourced-execution.md`](docs/event-sourced-execution.md). Venue matching assumptions are documented in [`docs/venue-simulator.md`](docs/venue-simulator.md). Isolated-margin, funding, interest, and liquidation assumptions are documented in [`docs/margin-funding-liquidation.md`](docs/margin-funding-liquidation.md).

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/check_public_boundary.py
ruff check .
mypy
pytest
```

## Security and disclosure policy

Do not commit credentials, runtime data, real infrastructure addresses, operator notes, production performance artifacts, or proprietary strategy implementations. The boundary scanner is intentionally conservative; use placeholders and synthetic examples in public documentation.

This project is research software. It does not provide investment advice and must not be assumed safe for live trading without independent validation and operational controls.
