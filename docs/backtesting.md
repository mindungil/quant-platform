# Minimal backtesting model

The public runner intentionally implements a small, inspectable execution model before adding broker- or venue-specific behavior.

## Timing convention

A strategy computes a complete target-position series once through `BatchAlphaPlugin.generate_positions`.

`positions[i]` is active from `bars[i].open` until `bars[i + 1].open`. This means information observed at the previous bar close can be applied at the next available open without using the future return being scored.

This convention follows the common market-order backtest assumption that an order created after a completed bar executes at the next bar open:

- Backtrader order execution: https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/

## Return and cost model

For each scored period:

```text
asset_return = next_open / current_open - 1
turnover = abs(target_position - previous_position)
gross_return = target_position * asset_return
cost_fraction = turnover * (fee_bps + slippage_bps) / 10_000
net_return = gross_return - cost_fraction
```

A reversal from `+1` to `-1` has turnover `2`, so both sides of the rebalance are charged. Fees and slippage remain separate configuration fields even though the minimal runner combines them into one linear variable cost.

Reference implementations also model fees and slippage as explicit execution inputs or separate reality models:

- VectorBT portfolio API: https://vectorbt.dev/api/portfolio/base/
- QuantConnect LEAN fee models: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/transaction-fees/supported-models
- QuantConnect LEAN slippage models: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/supported-models

## Reported output

The runner records every open-to-open period and summarizes:

- gross and net total return
- buy-and-hold benchmark return over the same opens
- annualized Sharpe ratio with a caller-supplied periods-per-year value
- maximum drawdown
- total turnover
- accumulated linear cost fraction
- ending active position

The result is deterministic for identical plugin output, bars, and configuration.

## Deliberate limitations

The minimal runner does not model:

- partial fills or order-book liquidity
- volume-dependent market impact
- limit, stop, or intrabar execution
- borrow fees, funding payments, or cash interest
- margin, leverage, or liquidation
- multiple assets or shared cash
- walk-forward splitting or statistical correction for repeated trials

These capabilities should be added as explicit models rather than silently assumed. In particular, a volume-aware slippage model should be introduced before treating large simulated orders as executable.
