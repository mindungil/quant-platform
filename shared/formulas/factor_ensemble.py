"""Factor Ensemble Formula — combines 45+ decorrelated alpha factors.

Replaces the single-formula selection approach with an institutional-grade
multi-factor ensemble. Each factor produces a [-1, 1] score. Factors are
grouped by category, weighted by regime, and combined into a final signal.

This formula always runs as the primary scoring engine. Individual formulas
(momentum, reversion, breakout) continue to run via MAB for style adjustment.
"""
import logging
import math
from shared.formulas.base import BaseFormula, FormulaResult
from shared.formulas.registry import formula_registry
from shared.regime import detect_regime
from shared.factors.dynamic_weights import load_category_weights, load_factor_weights

logger = logging.getLogger("factor-ensemble")

class FactorEnsembleFormula(BaseFormula):
    name = "factor_ensemble"
    best_regime = "any"
    description = "Multi-factor ensemble combining 45+ decorrelated alpha factors"

    def compute(self, features: dict) -> FormulaResult:
        from shared.factors import compute_all, get_by_category, ALL_FACTORS

        # 1. Compute all factors
        all_scores = compute_all(features)

        # 2. Detect regime for dynamic weighting
        regime = detect_regime(features, asset=features.get("asset"))
        regime_key = "trending" if "trend" in regime.trend_strength.lower() else \
                     "volatile" if "high" in regime.volatility.lower() else \
                     "sideways" if "sideways" in regime.trend_strength.lower() or "ranging" in regime.trend_strength.lower() else \
                     "default"

        # Load dynamic weights from Redis (falls back to defaults if unavailable)
        try:
            category_weights = load_category_weights()
            factor_w = load_factor_weights()
        except Exception as e:
            logger.debug("dynamic_weights_fallback: %s", e)
            from shared.factors.dynamic_weights import DEFAULT_CATEGORY_WEIGHTS, DEFAULT_FACTOR_WEIGHTS
            category_weights = DEFAULT_CATEGORY_WEIGHTS
            factor_w = DEFAULT_FACTOR_WEIGHTS

        # Check if a strategy preset override is active
        try:
            from shared.factors.dynamic_weights import get_active_protocol
            from shared.strategies.registry import STRATEGY_PRESETS
            protocol_name = get_active_protocol()
            if protocol_name in STRATEGY_PRESETS:
                preset = STRATEGY_PRESETS[protocol_name]
                weights = preset["category_weights"]
                logger.debug("using_strategy_preset_weights", extra={"preset": protocol_name})
            else:
                weights = category_weights.get(regime_key, category_weights.get("default", {}))
        except Exception as e:
            logger.debug("strategy_preset_weights_fallback: %s", e)
            weights = category_weights.get(regime_key, category_weights.get("default", {}))

        # 3. Aggregate by category with per-factor dynamic weights
        category_scores = {}
        category_counts = {}
        for factor in ALL_FACTORS:
            score = all_scores.get(factor.name, 0.0)
            fw = factor_w.get(factor.name, 1.0)  # dynamic per-factor weight
            weighted_score = score * fw
            cat = factor.category
            if cat not in category_scores:
                category_scores[cat] = 0.0
                category_counts[cat] = 0
            if score != 0.0:  # only count non-zero (data available)
                category_scores[cat] += weighted_score
                category_counts[cat] += 1

        # Average per category
        for cat in category_scores:
            if category_counts[cat] > 0:
                category_scores[cat] /= category_counts[cat]

        # 4. Regime-weighted combination
        weighted_sum = 0.0
        total_weight = 0.0
        for cat, avg_score in category_scores.items():
            w = weights.get(cat, 1.0)
            weighted_sum += avg_score * w
            total_weight += w

        final_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        final_score = max(-1.0, min(1.0, final_score))

        # 5. Confidence = factor agreement rate * signal strength
        valid_scores = [v for v in all_scores.values() if v != 0.0]
        if valid_scores:
            bullish = sum(1 for v in valid_scores if v > 0.05)
            bearish = sum(1 for v in valid_scores if v < -0.05)
            total = len(valid_scores)
            agreement = max(bullish, bearish) / total if total > 0 else 0
            confidence = agreement * min(abs(final_score) * 2, 1.0)
        else:
            confidence = 0.0

        # 6. Magnitude estimate (simple: score * base expected move)
        magnitude = final_score * 0.02  # assume 2% max expected move

        return FormulaResult(
            score=final_score,
            confidence=min(confidence, 1.0),
            components={**all_scores, **{f"cat_{k}": v for k, v in category_scores.items()}},
            formula_name="factor_ensemble",
            regime_label=regime.label,
        )


formula_registry.register(FactorEnsembleFormula())
