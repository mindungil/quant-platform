"""Intelligent formula selection using lightweight ML when sufficient data exists.

Falls back to heuristic (risk-adjusted composite score) when < 50 samples.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("crypto-agent")

# Minimum samples needed to train the ML selector
MIN_SAMPLES_FOR_ML = 50


@dataclass
class FormulaRanking:
    name: str
    predicted_score: float
    selection_method: str   # "ml_model" | "memory_heuristic" | "regime_default"
    confidence: float


def _extract_feature_vector(features: dict) -> list[float]:
    """Extract a fixed-size feature vector from market features dict."""
    # 10 features that characterize market state
    keys = [
        "rsi_14", "adx_14", "atr_14", "close", "sma_20",
        "bb_upper", "bb_lower", "macd", "macd_signal", "stochastic_k",
    ]
    vector = []
    for k in keys:
        val = features.get(k)
        vector.append(float(val) if val is not None else 0.0)
    return vector


def rank_formulas_ml(
    features: dict,
    memory_items: list[dict],
    formula_names: list[str],
) -> list[FormulaRanking]:
    """Rank formulas using ML if enough data, otherwise return empty (use heuristic).

    Each memory_item should have:
      - record.formula_name
      - record.trade_outcome
      - record with feature data (from components or stored features)

    Returns ranked list of FormulaRanking, best first. Empty if insufficient data.
    """
    # Collect training data: (features, formula_name) -> outcome
    X = []
    y = []
    formula_labels = []

    for item in memory_items:
        record = item.get("record", {})
        fname = record.get("formula_name")
        outcome = record.get("trade_outcome")
        if fname is None or outcome is None:
            continue

        # Use regime_label components as proxy features if detailed features not stored
        regime = record.get("regime_label", "")
        regime_parts = regime.split("_") if regime else []

        # Encode regime as simple features
        regime_features = [
            1.0 if "trending" in regime_parts else 0.0,
            1.0 if "sideways" in regime_parts else 0.0,
            1.0 if "high" in regime_parts else 0.0,
            1.0 if "low" in regime_parts else 0.0,
            1.0 if "bullish" in regime_parts else 0.0,
            1.0 if "bearish" in regime_parts else 0.0,
        ]

        X.append(regime_features)
        y.append(outcome)
        formula_labels.append(fname)

    if len(X) < MIN_SAMPLES_FOR_ML:
        return []  # not enough data, caller should use heuristic

    try:
        import numpy as np
        from sklearn.ensemble import GradientBoostingRegressor

        X_arr = np.array(X)
        y_arr = np.array(y)

        # Get current market regime features
        from shared.regime import detect_regime
        regime = detect_regime(features)
        regime_parts = regime.label.split("_")
        current_features = np.array([[
            1.0 if "trending" in regime_parts else 0.0,
            1.0 if "sideways" in regime_parts else 0.0,
            1.0 if "high" in regime_parts else 0.0,
            1.0 if "low" in regime_parts else 0.0,
            1.0 if "bullish" in regime_parts else 0.0,
            1.0 if "bearish" in regime_parts else 0.0,
        ]])

        # Train one model per formula, predict expected outcome
        rankings = []
        for fname in formula_names:
            mask = np.array([fl == fname for fl in formula_labels])
            if mask.sum() < 5:
                continue

            model = GradientBoostingRegressor(
                n_estimators=50,
                max_depth=3,
                learning_rate=0.1,
                random_state=42,
            )
            model.fit(X_arr[mask], y_arr[mask])
            predicted = float(model.predict(current_features)[0])

            rankings.append(FormulaRanking(
                name=fname,
                predicted_score=round(predicted, 6),
                selection_method="ml_model",
                confidence=min(mask.sum() / 100, 1.0),
            ))

        rankings.sort(key=lambda r: r.predicted_score, reverse=True)
        logger.info(
            "ml_formula_ranking",
            extra={
                "top_formula": rankings[0].name if rankings else "none",
                "n_formulas": len(rankings),
                "n_samples": len(X),
            },
        )
        return rankings

    except Exception as exc:
        logger.warning("ml_formula_ranking_failed", extra={"error": str(exc)})
        return []
