# Contributing

## Repository role

This repository is the canonical source for public, reusable Quant Platform code. Proprietary implementations belong in `quant-alpha`; deployment and operator material belong in `quant-ops`.

## Workflow

1. Create a branch named `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, or `chore/<topic>`.
2. Keep each pull request focused on one coherent change.
3. Add deterministic tests and update public documentation.
4. Run:

   ```bash
   python scripts/check_public_boundary.py
   ruff check .
   mypy
   pytest
   ```

5. Review the complete diff for private code, runtime data, credentials, host information, or production parameters.
6. Squash-merge the pull request so `main` contains one meaningful commit per change.

## Public extraction rule

A useful idea discovered in a private repository may be contributed here only by rewriting the general abstraction against public contracts. Do not copy private directory trees, Git history, production configuration, strategy parameters, or operational examples into this repository.

## Commit and pull-request titles

Use concise Conventional Commit-style titles, for example:

- `feat: add portfolio constraint protocol`
- `fix: reject non-finite signal scores`
- `docs: clarify plugin compatibility policy`
- `chore: update supported Python versions`

The squash commit should use the pull-request title.

## Release compatibility

Public releases use semantic versioning. Breaking protocol changes require a major-version change, compatible additions require a minor-version change, and fixes require a patch-version change. Private repositories must pin a tested compatible range or immutable revision.
