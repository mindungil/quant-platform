# Contributing

Thanks for taking the time to look at this project. The most common
contributions we'll merge are:

- A new alpha or factor (see [Bring Your Own Alpha](#bring-your-own-alpha))
- Bug fixes in `shared/` utilities (persistence, health, RLS, event bus)
- New backtest validators or metrics in `shared/backtest/`
- Docker / observability improvements

The proprietary alpha catalogue lives in a private repo and is not part
of this codebase. PRs that try to add specific production alpha logic
here will be redirected to that repo.

## Development setup

```bash
make venv            # create .venv
make operator-deps   # minimal Python deps for ops scripts
make install         # full deps + frontend
make compose-up      # bring up the local stack
make test            # unit tests per service
make smoke-e2e       # end-to-end smoke
```

The stack expects PostgreSQL, Redis, and NATS JetStream — all provisioned
by `docker-compose`.

## Bring Your Own Alpha

The platform exposes five `register_*` entry points and eight service-level
policy plugins. The simplest way to ship an alpha is:

1. Subclass `shared.alpha.base.Alpha`:

   ```python
   import pandas as pd
   from shared.alpha.base import Alpha, AlphaConfig

   class MyAlpha(Alpha):
       def _generate(self, df: pd.DataFrame) -> pd.Series:
           # ... your edge ...
           # return a Series in [-1, 1] aligned to df.index
           return signal
   ```

2. Register it in a module Python can import:

   ```python
   from shared.alpha.registry import register_alpha
   register_alpha("my_alpha", lambda cfg=None: MyAlpha(cfg))
   ```

3. Point the runtime at it:

   ```bash
   export QUANT_ALPHA_PLUGINS=my_pkg.alphas
   ```

A copy-pasteable example lives at `examples/sma_crossover_alpha.py`.

### Position-series contract

Your `_generate` returns the **target notional fraction** for each bar.
`+1.0` = fully long the asset, `-1.0` = fully short, `0.0` = flat.

The base class:

- Hard-clamps the output to `[-1, 1]`.
- Shifts by one bar to enforce no look-ahead (your decision at the close
  of bar `t` becomes the target *from* `t+1`).
- Optionally applies EMA smoothing if `position_smoothing` is set in the
  alpha config params.
- Applies the long-only mask and `max_gross_position` cap from
  `AlphaConfig`.

### Validation gate (alpha incubator)

New alphas must clear the institutional gate before reaching live capital:

| Metric | Threshold |
|--------|-----------|
| 8-year backtest Sharpe (net of costs) | ≥ 1.0 |
| OOS Sharpe (last 20%) | ≥ 0.7 |
| Max drawdown | ≤ 0.30 |
| \|IC_IR\| | ≥ 0.5 |
| Sharpe decay (full − OOS) | ≤ 0.5 |
| DSR verdict | `"genuine"` |
| PBO (combinatorially-symmetric CV) | ≤ 0.30 |

Run the incubator manually with:

```bash
make incubate-bulk-submit   # adds your alpha to the candidate table
make incubate-drain          # walks-forward + tests gates
make incubate-list           # see promotion status
```

Or wait for the daily cron daemon (`scripts/incubator_cron.sh`) to run it
automatically inside the `strategy-lab` container.

## Pull requests

- Title: `<area>(<scope>): <imperative description>` — same style as
  recent commits.
- Body: explain the *why*, not the *what*. Include backtest numbers if
  you're touching a signal path.
- Run `make test` and `make smoke` locally before submitting.
- For alpha PRs, attach the incubator output JSON.

## Reporting bugs

Open an issue with:

- The smallest repro you can produce (ideally a `pytest -k <name>`).
- The exact compose / git SHA you're running.
- For trading bugs, include the `correlation_id` from the relevant
  log entry — the event-bus traces are correlation-id keyed.

## Security

Found something exploitable? Don't open a public issue — email the
maintainer directly. Pre-coordinated disclosure of a real vulnerability
is always preferable to a 0-day in a public tracker.
