# Adaptive Formula Selection System — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Agent-driven dynamic formula selection with memory-based learning

---

## Core Concept

Instead of one fixed set of formulas, the system maintains a **formula library** with
multiple trading models. An **agent** detects the current market regime, queries **memory**
for which formulas performed best in similar conditions, selects the optimal formula,
executes it, and records the outcome back to memory for future learning.

```
Market Data → Regime Detector → Agent queries Memory
                                  ↓
                          "In past sideways BTC markets,
                           mean_reversion had Sharpe 2.1"
                                  ↓
                          Agent selects mean_reversion
                                  ↓
                          Signal scored with that formula
                                  ↓
                          Trade executed → Outcome recorded to Memory
                                  ↓
                          Next time similar regime → better selection
```

---

## 1. Formula Library (NEW: shared/formulas/)

A registry of pluggable scoring formulas. Each formula:
- Takes FeatureSnapshot as input
- Returns a score in [-1, 1]
- Has metadata: name, description, best_regime, indicators_required

### Formulas to implement:

| Name | Logic | Best Regime |
|------|-------|-------------|
| `momentum_ema_cross` | EMA(9) vs EMA(21) crossover, strength = distance/ATR | trending |
| `mean_reversion_bb` | Bollinger %B contrarian: buy when %B < 0.2, sell when > 0.8 | sideways |
| `rsi_divergence` | RSI extreme + price direction divergence | reversal |
| `macd_histogram` | MACD histogram momentum, normalized by ATR | trending |
| `volatility_breakout` | Price breaks ATR band from squeeze (low BB width) | breakout |
| `vwap_reversion` | Distance from VWAP as mean-reversion signal | intraday |
| `stochastic_momentum` | Stochastic K/D crossover in trend direction (ADX>25) | trending |
| `composite_adaptive` | Weighted blend selected by regime (current default) | any |

### Formula interface:

```python
class FormulaResult:
    score: float          # [-1, 1]
    confidence: float     # [0, 1]  
    components: dict      # breakdown

class BaseFormula:
    name: str
    description: str
    best_regime: str      # trending | sideways | reversal | breakout | any
    required_indicators: list[str]
    
    def compute(self, features: FeatureSnapshot) -> FormulaResult
```

---

## 2. Market Regime Detector (NEW: shared/regime.py)

Classifies current market state using existing indicators:

```python
def detect_regime(features: FeatureSnapshot) -> MarketRegime:
    regime = MarketRegime(
        trend_strength = classify_trend(adx_14),     # trending | sideways
        volatility = classify_volatility(atr_14, bb_width),  # low | normal | high
        momentum = classify_momentum(rsi_14, macd),  # bullish | bearish | neutral
    )
    # Composite label: e.g. "trending_high_vol_bullish"
    regime.label = f"{regime.trend_strength}_{regime.volatility}_{regime.momentum}"
    return regime
```

---

## 3. Memory Service Upgrade

### 3a. New memory_type: "formula_outcome"

Store which formula was used, in which regime, with what result:

```python
# New fields in MemoryRecord:
formula_name: str | None       # which formula was used
regime_label: str | None       # market regime at decision time  
trade_outcome: float | None    # realized PnL of the trade
outcome_sharpe: float | None   # rolling Sharpe after this trade
```

### 3b. Enhanced search: query by regime

```python
def search_formula_outcomes(regime_label: str, asset: str, top_k: int) -> list:
    """Find past formula outcomes in similar market regimes."""
    # Score by: regime match (0.4), asset match (0.3), recency (0.2), outcome (0.1)
```

### 3c. Reinforcement: update memory with trade outcomes

```python
def reinforce(memory_id: str, trade_outcome: float, outcome_sharpe: float):
    """Called when a trade closes. Updates the memory record with actual results."""
    record.trade_outcome = trade_outcome
    record.outcome_sharpe = outcome_sharpe
    record.last_reinforced_at = now()
```

---

## 4. Agent Decision Loop Upgrade (crypto-agent)

Current 6-phase loop becomes 8-phase:

```
Phase 1: GATHER    — fetch latest signal/features
Phase 2: DETECT    — classify market regime (NEW)
Phase 3: RECALL    — query memory for best formula in this regime (UPGRADED)  
Phase 4: SELECT    — agent picks formula based on memory + strategy (UPGRADED)
Phase 5: SCORE     — run selected formula on features (NEW)
Phase 6: CHECK     — risk pre-checks
Phase 7: EXECUTE   — build order, publish
Phase 8: RECORD    — save decision + formula choice to memory (UPGRADED)
```

### Phase 2 (DETECT):
```python
regime = regime_detector.detect(features)
# e.g. MarketRegime(trend="trending", volatility="high", momentum="bullish")
```

### Phase 3 (RECALL):
```python
memories = memory_client.search_formula_outcomes(
    regime_label=regime.label,
    asset=asset,
    top_k=10
)
# Returns: [("momentum_ema_cross", avg_outcome=0.03), ("mean_reversion_bb", avg_outcome=-0.01), ...]
```

### Phase 4 (SELECT):
```python
# Rank formulas by past performance in this regime
formula_scores = {}
for m in memories:
    if m.formula_name not in formula_scores:
        formula_scores[m.formula_name] = []
    formula_scores[m.formula_name].append(m.trade_outcome or 0)

# Pick best-performing formula, or fallback to composite_adaptive
best_formula = max(formula_scores, key=lambda f: mean(formula_scores[f]))
selected = formula_library.get(best_formula)
```

### Phase 5 (SCORE):
```python
result = selected.compute(features)
# FormulaResult(score=0.72, confidence=0.85, components={...})
```

---

## 5. Feedback Loop: Trade Outcome → Memory Reinforcement

When order-service fills or closes a trade:

```
order filled/closed → publish event "trade.completed"
  → statistics-service records PnL
  → memory-service reinforces the original decision's memory record
     with actual trade_outcome and outcome_sharpe
```

This creates the learning loop:
1. Agent picks formula X in regime Y
2. Trade executes, outcome = +3%
3. Memory records: "formula X in regime Y → +3%"
4. Next time regime Y occurs, memory says formula X works
5. Agent picks formula X again (or tries alternatives if X degraded)

---

## Implementation Plan

### Step 1: Formula Library (shared/formulas/)
- base.py: BaseFormula, FormulaResult, FormulaRegistry
- momentum.py: momentum_ema_cross, macd_histogram, stochastic_momentum  
- reversion.py: mean_reversion_bb, vwap_reversion, rsi_divergence
- breakout.py: volatility_breakout
- composite.py: composite_adaptive (current default logic, wrapped)

### Step 2: Regime Detector (shared/regime.py)
- detect_regime() using ADX, ATR, BB width, RSI, MACD

### Step 3: Memory Service Upgrade
- Add formula_name, regime_label, trade_outcome, outcome_sharpe to model
- Add search_formula_outcomes endpoint
- Add reinforce endpoint

### Step 4: Agent Loop Upgrade
- Add DETECT and SCORE phases
- Replace fixed strategy scoring with formula selection
- Record formula choice in memory

### Step 5: Feedback Loop
- order-service publishes trade.completed events
- memory-service listens and reinforces records
