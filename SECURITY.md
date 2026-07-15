# Security Policy

## Supported scope

The public repository contains reusable source code and examples only. Production credentials, infrastructure details, proprietary strategies, and runtime data are outside its scope.

## Reporting

Report suspected credential exposure, private-source leakage, unsafe order behavior, or a boundary-scanner bypass privately to the repository owner. Do not open a public issue containing secrets, host addresses, portfolio state, or exploit details.

## Repository hygiene

Before every release:

- run `python scripts/check_public_boundary.py`;
- verify no generated data or environment files are tracked;
- review dependency changes and lock release artifacts by digest;
- confirm examples use placeholders and synthetic data;
- inspect the complete diff, not only the current working tree.

Secrets must be revoked and rotated immediately if exposed. Removing a file from the latest commit does not remove it from Git history, forks, caches, artifacts, or existing clones.
