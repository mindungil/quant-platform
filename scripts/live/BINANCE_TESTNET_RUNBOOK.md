# Binance Futures Testnet 2-Week Shadow Trading Runbook

Goal: validate that the v4.4 signal → order bridge behaves on a real exchange
(maker fill rate, order rejection reasons, price slippage) before risking
real money. Target: 2 weeks of uninterrupted testnet execution whose final
P&L curve tracks the paper portfolio within ±1% (excluding known maker-fill
delta).

## Prerequisites

1. **Testnet account funded.** Create at https://testnet.binancefuture.com
   Fund via the faucet (10,000 USDT). Record the account ID.
2. **API key pair** generated with **Futures Trading = true**, IP whitelist
   set to this host's public IP.
3. **v4.4 engine running.** `crontab -l` should show both
   `generate_signals.py` and `paper_portfolio.py update` every hour, and
   signals in `data/signals/` should be < 2h old.
4. **Bridge dry-run clean.** `python3 scripts/live/signal_to_order_bridge.py
   --dry-run` should print planned positions with no errors.

## Phase A — wire the keys (Day 0, 10 min)

```bash
# 1. Store credentials in the credential-store (never commit these!)
export BINANCE_API_KEY=<your_testnet_key>
export BINANCE_API_SECRET=<your_testnet_secret>

# 2. Smoke test the connector
python3 -c "
import os
from shared.execution.binance_futures import BinanceFuturesConnector
c = BinanceFuturesConnector(
    api_key=os.environ['BINANCE_API_KEY'],
    api_secret=os.environ['BINANCE_API_SECRET'],
    testnet=True,
)
print('equity:', c.get_account_equity())
print('prices:', c.get_mark_prices(['BTCUSDT','ETHUSDT','BNBUSDT']))
"
```

Expected: equity ~10000 USDT, mark prices returned.

## Phase B — first manual run (Day 0, 15 min)

```bash
python3 scripts/live/signal_to_order_bridge.py --testnet \
  --api-key "$BINANCE_API_KEY" --api-secret "$BINANCE_API_SECRET" \
  --max-position-per-symbol 0.25 \
  --max-gross-exposure 1.5 \
  --max-drawdown-halt 0.15 \
  --equity-override 10000
```

**Exit cleanly means:** orders placed without rejection, reconciliation
message matches bridge log in `data/logs/execution/bridge_*.json`, and
`get_positions()` confirms the new state.

If any order is rejected, read the error in the log and fix the cause
(most common: symbol filters — min notional, step size) before moving on.

## Phase C — switch cron to testnet (Day 0)

Replace the dry-run cron line with testnet mode. Edit `crontab -e`:

```cron
20 * * * * cd /home/ubuntu/quant && \
  BINANCE_API_KEY=<key> BINANCE_API_SECRET=<secret> \
  /usr/bin/python3 scripts/live/signal_to_order_bridge.py --testnet \
  >> /home/ubuntu/quant/data/logs/bridge.log 2>&1
```

**Safety:** credentials in crontab is acceptable for testnet (no real
money); for mainnet use credential-store + systemd env-file.

## Phase D — daily monitoring (Days 1-14)

Every day at a fixed time, run:

```bash
# 1. Bridge log summary — fills, failures, notional
grep -E "execution: filled=" data/logs/bridge.log | tail -24

# 2. Compare paper vs testnet equity
python3 scripts/live/paper_portfolio.py status
# vs testnet equity (requires API call): re-run Phase A smoke

# 3. Fill rate audit — what fraction of intended orders actually filled?
jq '.filled, .failed' data/logs/execution/bridge_*.json | awk ...
```

### Red flags that halt testnet:

- **>10% of orders failing** for non-risk reasons (minNotional, stepSize,
  price not available). Means our bridge's quantity rounding or price
  source is wrong.
- **Testnet equity divergence > 3%** from paper portfolio over 3 consecutive
  days. Means our simulator is systematically optimistic/pessimistic.
- **Consecutive DD > 12%** (paper hit 12% max DD in Apr 2026; if we exceed
  that on testnet, the v4.4 additions regressed).

### Green lights:

- Daily fill rate ≥ 90% of active (non-parked) intended orders.
- Maker-fallback-to-taker rate ≤ 25% (tracked in `bridge_*.json`).
- Equity within ±1% of paper portfolio on any given day.

## Phase E — Go/No-go decision (Day 14)

```bash
# Reconcile paper and testnet P&L series
python3 scripts/live/paper_portfolio.py history > /tmp/paper.csv
# testnet history from bridge logs + connector.get_account_equity()

# Compute tracking error
python3 scripts/analysis/paper_vs_testnet_reconcile.py  # <- TO BE BUILT
```

**GO if:** tracking error < 1.5%, no red flags hit, all compliance gates
fired appropriately (at least one DD halt simulation tested).

**NO-GO if:** tracking error > 3%, or any unexplained fail rate spike.
Push the testnet window another 2 weeks before considering mainnet.

## Phase F — mainnet prerequisites (DO NOT run without all checked)

- [ ] 2-week testnet complete with GO verdict
- [ ] `alpha_gate.py --execution maker` still passes for BTC/ETH/BNB
- [ ] `drift_registry` baselines populated + Grafana dashboard reviewed
- [ ] Separate mainnet key stored in credential-store, IP whitelisted
- [ ] Small-size guard: `--max-gross-exposure 0.5` for first week
- [ ] Kill-switch drill: confirm `portfolio.kill_switch` set to true
      actually blocks `signal_to_order_bridge` submissions (requires
      portfolio-service `/portfolio/summary` wire-in from live checklist)
- [ ] `--confirm-live` flag understood by at least 2 humans

Only then:

```bash
python3 scripts/live/signal_to_order_bridge.py --live --confirm-live \
  --api-key "$BINANCE_API_KEY_MAINNET" \
  --api-secret "$BINANCE_API_SECRET_MAINNET" \
  --max-gross-exposure 0.5
```

## Emergency kill-switch (any phase)

```bash
# 1. Stop cron bridge entries (still generate signals, just don't execute)
crontab -l | grep -v signal_to_order_bridge | crontab -

# 2. Flatten all positions via exchange UI or direct script
python3 -c "
from shared.execution.binance_futures import BinanceFuturesConnector
import os
c = BinanceFuturesConnector(os.environ['BINANCE_API_KEY'],
                             os.environ['BINANCE_API_SECRET'],
                             testnet=True)  # or False for live
for sym, qty in c.get_positions().items():
    side = 'SELL' if qty > 0 else 'BUY'
    c.place_order(symbol=sym, side=side, quantity=abs(qty), order_type='MARKET', reduce_only=True)
"
```
