# Git Flow

This repository follows classic Git Flow during the productionization program.

## Permanent Branches

- `main`: release-ready code only
- `develop`: integration branch for the next release

## Supporting Branches

- `feature/<area>-<short-name>` for implementation slices
- `release/<version>` for release stabilization
- `hotfix/<issue>` for urgent fixes cut from `main`

## Merge Rules

- feature branches merge into `develop` by PR
- release branches merge into both `main` and `develop`
- hotfix branches merge into both `main` and `develop`
- direct pushes to `main` are avoided once `develop` is established

## Recommended Commit Sequence

1. `chore(platform): stabilize config and service contracts`
2. `feat(infra): add db migrations redis and jetstream bootstrap`
3. `feat(data): productionize market feature and signal pipeline`
4. `feat(memory): migrate memory and strategy registry to persistent stores`
5. `feat(agent): complete crypto decision loop and reasoning flow`
6. `feat(exec): add live-ready risk credential exchange and order path`
7. `feat(state): persist portfolio statistics and orchestration flows`
8. `feat(frontend): add auth gateway websocket and product dashboard`
9. `feat(observability): metrics logging ci and smoke tests`
10. `docs(release): update runtime and operator documentation`
