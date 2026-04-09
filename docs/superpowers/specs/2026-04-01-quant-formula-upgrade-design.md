# Quant Formula Upgrade — Design Spec

**Date:** 2026-04-01
**Status:** Draft
**Scope:** All quantitative formulas across 8 services

---

## 1. Signal Generation (signal-service)

### Current Problem
- Binary direction signals (1.0 / -1.0) for MACD, SMA, VWAP
- No confidence weighting — weak RSI signal treated same as strong
- Simple arithmetic mean of all components

### Upgrade

**1a. Confidence-Weighted Signal Scoring**

Replace binary signals with continuous confidence scores:

```
RSI:    confidence = (RSI - 50) / 50                    # already done, keep
MACD:   confidence = tanh(histogram / ATR)              # normalized by volatility
SMA_20: confidence = (close - SMA_20) / (ATR * sqrt(20))  # distance in ATR units
VWAP:   confidence = (close - VWAP) / (ATR * sqrt(N))
BB:     confidence = (close - mid) / (upper - mid)      # Bollinger %B mapped to [-1,1]
```

**1b. Exponentially Weighted Combination**

Replace arithmetic mean with strategy-weight-aware combination:

```
raw_score = sum(weight_i * confidence_i) / sum(|weight_i|)
```

This preserves the existing strategy weights from strategy-registry while using proper confidence values.

**1c. ATR Calculation (new dependency)**

Add ATR (Average True Range) to feature-store output:
```
TR = max(high - low, |high - prev_close|, |low - prev_close|)
ATR_14 = EWM(TR, span=14)
```

**Files to modify:**
- `feature-store/app/core/indicators.py` — add ATR_14
- `feature-store/app/models/feature.py` — add atr_14 field
- `signal-service/app/core/scoring.py` — rewrite scoring logic

---

## 2. Position Sizing — Real Kelly Criterion (crypto-agent)

### Current Problem
- `position_size = |signal_score| * 0.25 * portfolio_balance` — just linear scaling
- No win probability, no payoff ratio

### Upgrade

**2a. Full Kelly Formula**

```
f* = (p * b - q) / b

where:
  p = historical win rate for this strategy+asset (from statistics-service)
  q = 1 - p
  b = avg_win / avg_loss (payoff ratio from statistics-service)
  f* = optimal fraction of capital
```

**2b. Half-Kelly for Safety**

Production systems use fractional Kelly to reduce variance:
```
position_fraction = f* * kelly_fraction   # kelly_fraction = 0.5 (half-Kelly)
position_fraction = clamp(position_fraction, 0, max_position_pct)
```

**2c. Volatility Adjustment**

Scale position inversely with recent volatility:
```
vol_scalar = target_vol / realized_vol_20d
position_fraction = position_fraction * min(vol_scalar, 1.5)
```

Where `realized_vol_20d = std(daily_returns, 20) * sqrt(252)` and `target_vol = 0.15` (15% annualized).

**2d. Statistics Integration**

Fetch win_rate, avg_win, avg_loss from statistics-service per strategy. Falls back to defaults (p=0.55, b=1.5) when insufficient data (<30 trades).

**Files to modify:**
- `crypto-agent/app/core/engine.py` — rewrite `_calculate_position_size()`
- `crypto-agent/app/core/config.py` — add kelly_fraction, target_vol settings
- `statistics-service/app/core/engine.py` — expose per-strategy stats

---

## 3. Backtest Engine (backtest-service)

### Current Problem
- Zero slippage, zero commission — inflates returns 20-50%
- Hardcoded Sharpe threshold (1.1) for pass/fail
- No take-profit in simulation
- Single-period backtest (no walk-forward)

### Upgrade

**3a. Transaction Cost Model**

```
slippage_bps = base_slippage + volume_impact
  base_slippage = 5 bps (configurable)
  volume_impact = 0 (simplified; real model needs order book depth)

commission_bps = exchange_fee_bps  # default 10 bps (0.1%)

total_cost_per_trade = (slippage_bps + commission_bps) / 10000
effective_entry = entry_price * (1 + total_cost_per_trade)  # for BUY
effective_exit  = exit_price * (1 - total_cost_per_trade)   # for SELL
```

**3b. Take-Profit in Simulation**

Add take-profit exit alongside stop-loss:
```
if pnl_pct >= take_profit_pct:
    close position (take profit)
elif pnl_pct <= -stop_loss_pct:
    close position (stop loss)
elif opposite signal:
    close position (signal reversal)
```

**3c. Trailing Stop in Simulation**

Track highest/lowest price since entry:
```
if side == BUY:
    highest = max(highest, current_price)
    trailing_trigger = highest * (1 - trailing_stop_pct)
    if current_price <= trailing_trigger:
        close position
```

**3d. Walk-Forward Validation**

Split data into windows:
```
total_bars = len(candles)
train_ratio = 0.7
n_windows = 3

For each window:
  train on [start:split]
  test on [split:end]
  slide forward

Final metrics = average across out-of-sample windows
```

This catches overfitting — strategies that pass in-sample but fail out-of-sample.

**3e. Enhanced Pass/Fail Criteria**

```
PASSED if:
  - out_of_sample_sharpe >= 0.8  (lowered from 1.1 since costs now included)
  - max_drawdown <= 0.20
  - win_rate >= 0.45  (lowered since we now model costs)
  - trade_count >= 10  (minimum statistical significance)
  - profit_factor >= 1.2  (gross_profit / gross_loss)
```

**3f. New Metrics**

Add to BacktestResult:
- `profit_factor`: gross_profit / gross_loss
- `calmar_ratio`: annualized_return / max_drawdown
- `avg_win`, `avg_loss`: for Kelly inputs
- `total_commission`: total cost deducted
- `out_of_sample_sharpe`: walk-forward Sharpe

**Files to modify:**
- `backtest-service/app/core/evaluator.py` — rewrite simulation loop
- `backtest-service/app/core/config.py` — add slippage_bps, commission_bps, take_profit_pct, trailing_stop_pct
- `backtest-service/app/models/backtest.py` — extend BacktestResult

---

## 4. Statistics Service — Correct Formulas

### Current Problem
- Population variance (divides by n, not n-1)
- No annualization on Sharpe/Sortino
- Downside variance formula wrong: uses `sum(pnl^2)` instead of `sum((pnl - target)^2)`
- No risk-free rate

### Upgrade

**4a. Corrected Sharpe Ratio**

```
excess_returns = returns - risk_free_rate_daily
sharpe = mean(excess_returns) / std(excess_returns, ddof=1) * sqrt(252)

risk_free_rate_daily = (1 + annual_rf_rate)^(1/252) - 1
annual_rf_rate = 0.05 (5%, configurable via env)
```

**4b. Corrected Sortino Ratio**

```
downside_returns = [r for r in excess_returns if r < 0]
downside_deviation = sqrt(sum((r - 0)^2 for r in downside_returns) / (n - 1))
sortino = mean(excess_returns) / downside_deviation * sqrt(252)
```

**4c. New Metrics**

- `profit_factor = sum(winning_pnls) / abs(sum(losing_pnls))`
- `calmar_ratio = annualized_return / max_drawdown`
- `avg_win`, `avg_loss` — for Kelly criterion
- `payoff_ratio = avg_win / abs(avg_loss)`
- `expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)`

**4d. Per-Strategy Statistics**

Add `strategy_id` to StatisticsInput. Store and retrieve per-strategy stats so Kelly criterion can query them.

**Files to modify:**
- `statistics-service/app/core/engine.py` — rewrite formulas
- `statistics-service/app/models/statistics.py` — extend models
- `statistics-service/app/api/routes.py` — add per-strategy endpoint

---

## 5. Risk Management — VaR/CVaR (risk-service)

### Current Problem
- Only static drawdown thresholds (5%, 10%)
- No forward-looking risk metrics
- No volatility-adjusted limits

### Upgrade

**5a. Parametric VaR (Value at Risk)**

```
VaR_95 = -portfolio_value * (mu - z_95 * sigma)
VaR_99 = -portfolio_value * (mu - z_99 * sigma)

where:
  mu = mean daily return (from trade history)
  sigma = std of daily returns
  z_95 = 1.645, z_99 = 2.326
```

**5b. Conditional VaR (Expected Shortfall)**

```
CVaR_95 = -portfolio_value * (mu - sigma * pdf(z_95) / (1 - 0.95))

Simplified: CVaR_95 = VaR_95 * 1.4 (approximation for normal distribution)
```

**5c. Volatility-Adjusted Drawdown Limits**

Replace static 5%/10% thresholds with dynamic ones:
```
vol_ratio = realized_vol_20d / long_term_vol
warning_threshold = base_warning * vol_ratio    # tighter in high-vol
liquidate_threshold = base_liquidate * vol_ratio

base_warning = 0.05
base_liquidate = 0.10
```

**5d. Enhanced Risk Response**

Add to RiskApprovalResponse:
- `var_95`: current portfolio VaR at 95%
- `cvar_95`: conditional VaR
- `volatility_regime`: "low" | "normal" | "high"

**5e. Risk Settings Per User**

Store per-user risk parameters in DB (replacing hardcoded defaults):
- max_notional, exposure_limit, max_drawdown thresholds
- risk_free_rate, target_volatility

**Files to modify:**
- `risk-service/app/core/engine.py` — add VaR/CVaR calculations
- `risk-service/app/models/risk.py` — extend response model
- `risk-service/app/db/repository.py` — add risk_settings table
- `risk-service/app/api/routes.py` — CRUD for user risk settings

---

## 6. Portfolio Analytics (portfolio-service)

### Current Problem
- Only calculates notional exposure
- No unrealized P&L
- Hardcoded rebalance threshold ($100k)

### Upgrade

**6a. Realized & Unrealized P&L**

```
unrealized_pnl = sum((current_price - avg_entry_price) * quantity for each position)
realized_pnl = sum of all closed trade P&Ls (from fills history)
total_pnl = realized_pnl + unrealized_pnl
```

Requires current market prices — fetch from market-data service.

**6b. Return Metrics**

```
daily_return = (total_equity_today - total_equity_yesterday) / total_equity_yesterday
total_return_pct = (total_equity - initial_capital) / initial_capital
```

**6c. Concentration Risk**

```
for each position:
  weight = position_value / total_exposure
  if weight > max_single_asset_weight (default 30%):
    flag rebalance_needed
```

**6d. Enhanced PortfolioSnapshot**

Add fields:
- `unrealized_pnl`, `realized_pnl`, `total_pnl`
- `daily_return_pct`
- `concentration`: dict of asset -> weight%
- `largest_position`: asset name with highest weight

**Files to modify:**
- `portfolio-service/app/db/repository.py` — add P&L calculations
- `portfolio-service/app/models/portfolio.py` — extend snapshot model
- `portfolio-service/app/api/routes.py` — add market price fetching

---

## 7. Feature Store — ATR & Additional Indicators

### Current Problem
- Missing ATR (needed for signal confidence normalization)
- No ADX (trend strength)
- No OBV (volume confirmation)

### Upgrade

**7a. ATR (Average True Range)**

```
TR_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
ATR_14 = EWM(TR, span=14)
```

**7b. ADX (Average Directional Index)**

```
+DM = high_i - high_{i-1} if positive and > |low_{i-1} - low_i| else 0
-DM = low_{i-1} - low_i if positive and > |high_i - high_{i-1}| else 0
+DI = EWM(+DM, 14) / ATR_14 * 100
-DI = EWM(-DM, 14) / ATR_14 * 100
DX = |+DI - -DI| / (+DI + -DI) * 100
ADX = EWM(DX, 14)
```

ADX > 25 = trending market, ADX < 20 = ranging.
Signal-service can use this: only trust momentum signals when ADX > 25.

**7c. OBV (On-Balance Volume)**

```
OBV_i = OBV_{i-1} + (volume if close > prev_close else -volume if close < prev_close else 0)
```

Volume confirmation for price moves.

**Files to modify:**
- `feature-store/app/core/indicators.py` — add ATR, ADX, OBV
- `feature-store/app/models/feature.py` — add new fields

---

## 8. Backtest Result → Strategy Activation Gate

### Current Problem
- Backtest pass/fail is disconnected from position sizing inputs
- No feedback loop from live performance to strategy parameters

### Upgrade

**8a. Backtest Results Feed Kelly**

When backtest completes, store win_rate, avg_win, avg_loss, payoff_ratio in strategy metadata.
These become the initial Kelly inputs for live trading.

**8b. Live Performance Updates Kelly**

Statistics-service periodically updates per-strategy stats.
Crypto-agent fetches latest stats before each decision — Kelly adapts over time.

**Files to modify:**
- `backtest-service/app/core/evaluator.py` — emit Kelly inputs in result
- `strategy-registry` — store Kelly params in strategy record
- `crypto-agent/app/core/engine.py` — fetch live stats for Kelly

---

## Implementation Order

The upgrades have dependencies:

```
Phase 1 (Foundation):
  7. Feature Store (ATR, ADX, OBV)  ← needed by signals
  4. Statistics Service corrections  ← needed by Kelly

Phase 2 (Core Math):
  1. Signal Generation (confidence scoring)
  2. Position Sizing (real Kelly)
  3. Backtest Engine (costs, walk-forward)

Phase 3 (Risk & Portfolio):
  5. Risk Management (VaR/CVaR)
  6. Portfolio Analytics (P&L)
  8. Feedback Loop (backtest → Kelly → live)
```

---

## Testing Strategy

Each upgraded formula gets:
1. **Unit test** with known inputs/outputs (hand-calculated expected values)
2. **Property test** — e.g., Sharpe should increase when mean return increases
3. **Integration test** — end-to-end flow still works after formula change
4. **Regression test** — existing backtest on known data produces expected (different but correct) results

---

## Non-Goals

- Machine learning models for signal generation (future work)
- Real-time streaming indicator calculation (current batch is sufficient)
- Multi-asset correlation matrix (future work)
- Options Greeks (no derivatives trading)
