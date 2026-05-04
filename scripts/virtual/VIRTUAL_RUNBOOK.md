# Virtual Futures Runbook

Goal: exercise the production signal → order → exchange code path using an
**in-memory simulator** that is hard-isolated from real/paper state. No API
keys, no external accounts, fully reproducible.

## What this is NOT

- **Not the paper portfolio** (`data/paper/`) — that is an older, simpler
  tool that fills at signal price. It still exists and runs in parallel.
- **Not Binance testnet** (`--testnet`) — that uses a real Binance account
  with synthetic funds and exercises the real Binance matching engine.
- **Not live** (`--live`) — that uses real money on mainnet.

## Hard isolation contract

Virtual state **only ever writes to**:
- `data/virtual/state.json`
- `data/virtual/history.jsonl`
- `data/virtual/IS_VIRTUAL_NOT_REAL.txt` (tripwire marker)
- `data/logs/virtual_execution/*.json`

The connector's constructor **refuses** to accept a state/history path
that does not contain `/virtual/` in its string form. Any attempt to
point it at `data/paper/` or `data/logs/execution/` raises a `ValueError`
immediately. This makes it impossible to cross-contaminate.

## First-time setup

```bash
# 1. Initialize (only needed once, or after a reset)
python3 scripts/virtual/init.py --equity 10000

# 2. Verify isolation marker
cat data/virtual/IS_VIRTUAL_NOT_REAL.txt

# 3. Make sure signals are recent (< 2h)
ls -lht data/signals/ | head -3

# 4. One-shot bridge run
python3 scripts/live/signal_to_order_bridge.py --virtual \
    --max-position-per-symbol 0.40 --max-gross-exposure 2.0
```

Expected: active symbols get filled, parked symbols stay flat, log under
`data/logs/virtual_execution/`.

## Continuous operation (cron)

`scripts/live/cron_setup.sh` now includes an hourly `--virtual` run at
`:20` past the hour. After running `bash scripts/live/cron_setup.sh`:

```cron
5  * * * *  generate_signals.py (hourly signals)
10 * * * *  paper_portfolio.py update (legacy paper — still runs)
20 * * * *  signal_to_order_bridge.py --virtual (NEW — virtual sim)
```

Virtual + paper run side by side. They produce two independent P&L series
both consuming the same signals; cross-checking them catches bugs in
either implementation.

## Daily monitoring

```bash
# 1. Status (equity, positions, UPL)
python3 scripts/virtual/status.py

# 2. Compare to paper portfolio (tracking error check)
python3 scripts/virtual/compare_paper.py --days 14

# 3. Bridge log — orders that were rejected / filled
tail -n 50 data/logs/bridge_virtual.log
```

### Red flags

- **status shows REJECTED orders piling up** — check
  `data/virtual/history.jsonl` for the `reason` field. Common causes:
  `stepSize rounds to zero` (signal asked for too-small qty) or
  `notional < minNotional` (sub-$5 orders on Binance Futures).
- **compare_paper Δ% > 3%** consistent — the two sims have diverged
  materially. Usually means either: (a) virtual has a slippage/fee
  difference, (b) paper is filling symbols virtual refuses, or (c) one
  was initialized at a different time. Inspect the history files.
- **equity going down while paper goes up (or vice versa)** — same as
  above, investigate immediately.

### Green lights

- Δ% between paper and virtual within ±2% (any difference > that is
  attributable to explicit realism settings — slippage, partial fills,
  queued limits).
- No REJECTED orders (or documented reasons only).
- Open orders count stays bounded when `limit_queue_enabled`.

## Resetting

```bash
# Safe (prompts for 'yes')
python3 scripts/virtual/reset.py --equity 10000

# Automated (no prompt)
python3 scripts/virtual/reset.py --equity 10000 --yes

# Archive old history before wiping (keeps for later diff)
python3 scripts/virtual/reset.py --equity 10000 --yes --archive
```

## Enabling realism features

By default the virtual connector runs in V1 "ideal" mode (immediate fills
at mark, no slippage, no queue). To model realistic execution, pass a
`RealismConfig` to the connector. Example from a REPL:

```python
from shared.execution.virtual_futures import (
    VirtualFuturesConnector, RealismConfig,
)

realism = RealismConfig(
    slippage_enabled=True,
    slippage_bps_per_10k_usd=0.5,
    slippage_max_bps=10.0,
    limit_queue_enabled=True,
    limit_fill_prob=0.7,     # 70% queue position
    partial_fill_enabled=True,
    partial_fill_threshold_pct=0.40,
    partial_fill_max_pct=0.70,
)
c = VirtualFuturesConnector(realism=realism)
```

**Future work (not yet wired into the bridge):** the CLI currently has no
`--realism <profile>` flag. To use realism in cron, either:
1. Edit `scripts/live/signal_to_order_bridge.py::run_virtual` to inject
   a custom RealismConfig, or
2. Wait for a `realism` field in `config/v4_production.json` (next
   iteration; tracked as a follow-up).

## Promoting from virtual to testnet/live

Virtual validates: bridge logic, quantity rounding, risk-limit
interaction, PnL accounting, position tracker.

Virtual does NOT validate: real API signing, real L2 queue dynamics, real
rate limits, network latency, exchange-side order rejection reasons.

So before moving to real money:
1. Run virtual for ≥ 1 week with no red flags.
2. Then run Binance testnet (`--testnet`) for ≥ 2 weeks —
   see `scripts/live/BINANCE_TESTNET_RUNBOOK.md`.
3. Only after both pass: `--live --confirm-live` with small
   `--max-gross-exposure`.

## Emergency stop (any time)

```bash
# Remove the cron entry (signals keep generating, execution stops)
crontab -l | grep -v 'signal_to_order_bridge.py --virtual' | crontab -

# Flatten all virtual positions
python3 -c "
import sys; sys.path.insert(0, '.')
from shared.execution.virtual_futures import VirtualFuturesConnector
c = VirtualFuturesConnector(reset=False)
for sym, q in c.get_positions().items():
    side = 'SELL' if q > 0 else 'BUY'
    c.place_market_order(sym, side, abs(q))
print('flattened.')
"
```

## Directory map

```
data/virtual/                       ← VIRTUAL ONLY, never real
  state.json                        current balance, positions, counters
  history.jsonl                     every fill/reject/expire event
  IS_VIRTUAL_NOT_REAL.txt           tripwire (human-readable)
  history_archived_<ts>.jsonl       past resets, if --archive used

data/logs/virtual_execution/        bridge-level logs (virtual mode)
data/logs/execution/                bridge-level logs (testnet + live)
data/logs/dry_run_execution/        bridge-level logs (dry-run mode)
data/paper/                         legacy paper portfolio (independent)
```
