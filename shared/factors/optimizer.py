"""Factor Weight Optimizer — adjusts weights based on prediction accuracy.

Runs daily. Analyzes which factors correctly predicted price movements.
Increases weights for accurate factors, decreases for inaccurate ones.
"""
import logging
import math

logger = logging.getLogger("factor-optimizer")


def optimize_factor_weights(
    verified_decisions: list[dict],
    current_weights: dict,
) -> dict:
    """Compute new factor weights from verified decision history.

    Each decision should have:
      - components: dict of factor_name -> score
      - price_change_pct: actual price change after decision
      - action: BUY/SELL/HOLD

    Returns updated factor weights dict.
    """
    if len(verified_decisions) < 10:
        logger.info("optimizer_skip: insufficient data (%d decisions)", len(verified_decisions))
        return current_weights

    # Track each factor's prediction accuracy
    factor_stats = {}  # factor_name -> {"correct": 0, "total": 0}

    for decision in verified_decisions:
        components = decision.get("components", {})
        price_change = decision.get("price_change_pct", 0)

        for factor_name, factor_score in components.items():
            if factor_name.startswith("cat_") or factor_name in ("ensemble_score", "style_score", "style_formula", "formula_confidence"):
                continue
            if not isinstance(factor_score, (int, float)):
                continue
            if factor_score == 0.0:
                continue

            if factor_name not in factor_stats:
                factor_stats[factor_name] = {"correct": 0, "total": 0, "sum_score": 0}

            factor_stats[factor_name]["total"] += 1

            # Factor was correct if: positive score + price went up, or negative score + price went down
            if (factor_score > 0 and price_change > 0) or (factor_score < 0 and price_change < 0):
                factor_stats[factor_name]["correct"] += 1

            factor_stats[factor_name]["sum_score"] += abs(factor_score)

    # Compute new weights
    new_weights = dict(current_weights)

    for factor_name, stats in factor_stats.items():
        if stats["total"] < 5:
            continue  # not enough data for this factor

        accuracy = stats["correct"] / stats["total"]
        current_w = current_weights.get(factor_name, 1.0)

        if accuracy > 0.55:
            # Good predictor — increase weight
            multiplier = 1.0 + (accuracy - 0.5) * 2  # max ~2.0x at 100% accuracy
            new_weights[factor_name] = min(current_w * multiplier, 3.0)
        elif accuracy < 0.45:
            # Bad predictor — decrease weight
            multiplier = 1.0 - (0.5 - accuracy) * 2  # min ~0.0x at 0% accuracy
            new_weights[factor_name] = max(current_w * multiplier, 0.1)
        # else: keep current weight (near 50% = random = neutral)

    logger.info("factor_weights_optimized", extra={
        "factors_analyzed": len(factor_stats),
        "factors_boosted": sum(1 for f, s in factor_stats.items() if s["total"] >= 5 and s["correct"]/s["total"] > 0.55),
        "factors_reduced": sum(1 for f, s in factor_stats.items() if s["total"] >= 5 and s["correct"]/s["total"] < 0.45),
    })

    return new_weights


def optimize_category_weights(
    verified_decisions: list[dict],
    current_category_weights: dict,
    factor_to_category: dict,
) -> dict:
    """Optimize per-regime category weights."""
    # Group decisions by regime
    by_regime = {}
    for d in verified_decisions:
        regime = d.get("regime", "default")
        # Simplify regime to key
        key = "trending" if "trend" in regime else "sideways" if "sideways" in regime or "ranging" in regime else "volatile" if "volatile" in regime or "high" in regime else "default"
        by_regime.setdefault(key, []).append(d)

    new_weights = {}
    for regime_key, decisions in by_regime.items():
        if len(decisions) < 5:
            new_weights[regime_key] = current_category_weights.get(regime_key, current_category_weights["default"])
            continue

        # Compute category accuracy for this regime
        cat_stats = {}
        for d in decisions:
            components = d.get("components", {})
            price_change = d.get("price_change_pct", 0)
            for fname, fscore in components.items():
                cat = factor_to_category.get(fname)
                if not cat or not isinstance(fscore, (int, float)) or fscore == 0:
                    continue
                cat_stats.setdefault(cat, {"correct": 0, "total": 0})
                cat_stats[cat]["total"] += 1
                if (fscore > 0 and price_change > 0) or (fscore < 0 and price_change < 0):
                    cat_stats[cat]["correct"] += 1

        regime_w = dict(current_category_weights.get(regime_key, current_category_weights["default"]))
        for cat, stats in cat_stats.items():
            if stats["total"] < 3:
                continue
            accuracy = stats["correct"] / stats["total"]
            curr = regime_w.get(cat, 1.0)
            if accuracy > 0.55:
                regime_w[cat] = min(curr * 1.2, 3.0)
            elif accuracy < 0.45:
                regime_w[cat] = max(curr * 0.8, 0.2)

        new_weights[regime_key] = regime_w

    # Fill missing regimes
    for key in current_category_weights:
        if key not in new_weights:
            new_weights[key] = current_category_weights[key]

    return new_weights
