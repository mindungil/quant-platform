"""Intelligent formula selection using lightweight ML when sufficient data exists.

Falls back through: ML model -> historical mean -> regime default.
Uses RandomForest with TimeSeriesSplit CV, numeric regime features,
rolling performance tracking, and model persistence.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger("crypto-agent")

# Training thresholds
MIN_SAMPLES_FOR_ML = 100
MIN_SAMPLES_PER_FORMULA = 10
RETRAIN_INTERVAL = 50  # retrain when this many new samples arrive


@dataclass
class FormulaRanking:
    name: str
    predicted_score: float
    selection_method: str   # "ml_model" | "historical_mean" | "regime_default"
    confidence: float


@dataclass
class _CachedModel:
    """Cached trained model with metadata for staleness detection."""
    model: object
    data_hash: str
    sample_count: int
    trained_at: float = field(default_factory=time.monotonic)


# Module-level model cache: formula_name -> _CachedModel
_model_cache: dict[str, _CachedModel] = {}


def _compute_data_hash(n_samples: int, last_outcomes: list[float]) -> str:
    """Quick hash to detect if training data has meaningfully changed."""
    raw = f"{n_samples}:{','.join(f'{v:.4f}' for v in last_outcomes[-20:])}"
    return hashlib.md5(raw.encode()).hexdigest()


def _extract_numeric_regime_features(regime_label: str, record: dict) -> list[float]:
    """Extract numeric regime features instead of crude one-hot encoding.

    Returns a fixed-size vector of 6 floats:
      [volatility_level, trend_strength, momentum_score,
       atr_normalized, rsi_normalized, adx_normalized]
    """
    parts = regime_label.split("_") if regime_label else []

    # Volatility level: continuous 0-1
    if "high" in parts:
        vol_level = 0.9
    elif "low" in parts:
        vol_level = 0.1
    else:
        vol_level = 0.5

    # Trend strength: continuous -1 to 1
    if "trending" in parts:
        trend_strength = 0.8
    elif "sideways" in parts:
        trend_strength = -0.5
    else:
        trend_strength = 0.0

    # Momentum score: continuous -1 to 1
    if "bullish" in parts:
        momentum = 0.7
    elif "bearish" in parts:
        momentum = -0.7
    else:
        momentum = 0.0

    # Use stored numeric indicators if available (normalized)
    atr_norm = 0.0
    rsi_norm = 0.0
    adx_norm = 0.0

    atr_val = record.get("atr_14") or record.get("atr")
    if atr_val is not None:
        close = record.get("close", 1.0) or 1.0
        atr_norm = min(float(atr_val) / float(close), 0.1) * 10  # scale to ~[0, 1]

    rsi_val = record.get("rsi_14") or record.get("rsi")
    if rsi_val is not None:
        rsi_norm = (float(rsi_val) - 50.0) / 50.0  # scale to [-1, 1]

    adx_val = record.get("adx_14") or record.get("adx")
    if adx_val is not None:
        adx_norm = min(float(adx_val) / 50.0, 1.0)  # scale to [0, 1]

    return [vol_level, trend_strength, momentum, atr_norm, rsi_norm, adx_norm]


def _compute_rolling_performance(
    memory_items: list[dict],
    formula_name: str,
    window: int = 10,
) -> float:
    """Compute rolling mean outcome for the last `window` trades of a formula."""
    outcomes = []
    for item in memory_items:
        record = item.get("record", {})
        if record.get("formula_name") == formula_name:
            outcome = record.get("trade_outcome")
            if outcome is not None:
                outcomes.append(float(outcome))
    if not outcomes:
        return 0.0
    recent = outcomes[-window:]
    return sum(recent) / len(recent)


def _needs_retrain(formula_name: str, current_hash: str, current_count: int) -> bool:
    """Check if a cached model needs retraining."""
    cached = _model_cache.get(formula_name)
    if cached is None:
        return True
    if cached.data_hash == current_hash:
        return False  # identical data
    if current_count - cached.sample_count >= RETRAIN_INTERVAL:
        return True
    return False


def _rank_by_historical_mean(
    memory_items: list[dict],
    formula_names: list[str],
    regime_label: str,
) -> list[FormulaRanking]:
    """Fallback: rank formulas by their historical mean outcome in the current regime."""
    rankings = []
    for fname in formula_names:
        outcomes = []
        regime_match_count = 0
        for item in memory_items:
            record = item.get("record", {})
            if record.get("formula_name") != fname:
                continue
            outcome = record.get("trade_outcome")
            if outcome is None:
                continue
            outcomes.append(float(outcome))
            if record.get("regime_label", "") == regime_label:
                regime_match_count += 1

        if not outcomes:
            continue

        mean_outcome = sum(outcomes) / len(outcomes)
        # Weight by regime relevance
        regime_weight = min(regime_match_count / max(len(outcomes), 1), 1.0)
        confidence = min(len(outcomes) / MIN_SAMPLES_FOR_ML, 0.7) * (0.5 + 0.5 * regime_weight)

        rankings.append(FormulaRanking(
            name=fname,
            predicted_score=round(mean_outcome, 6),
            selection_method="historical_mean",
            confidence=round(confidence, 3),
        ))

    rankings.sort(key=lambda r: r.predicted_score, reverse=True)
    return rankings


def _rank_by_regime_default(
    formula_names: list[str],
    regime_label: str,
) -> list[FormulaRanking]:
    """Last-resort fallback: score formulas by regime-formula type affinity."""
    parts = regime_label.split("_") if regime_label else []
    rankings = []
    for fname in formula_names:
        fname_lower = fname.lower()
        score = 0.0
        # Heuristic matching between formula name and regime
        if "trending" in parts and any(k in fname_lower for k in ("trend", "ema", "sma", "macd")):
            score = 0.5
        elif "sideways" in parts and any(k in fname_lower for k in ("mean_rev", "rsi", "bb", "range")):
            score = 0.5
        elif "high" in parts and any(k in fname_lower for k in ("breakout", "momentum", "vol")):
            score = 0.4
        elif "bullish" in parts and any(k in fname_lower for k in ("long", "bull", "buy")):
            score = 0.3
        elif "bearish" in parts and any(k in fname_lower for k in ("short", "bear", "sell")):
            score = 0.3

        rankings.append(FormulaRanking(
            name=fname,
            predicted_score=round(score, 6),
            selection_method="regime_default",
            confidence=0.1,
        ))

    rankings.sort(key=lambda r: r.predicted_score, reverse=True)
    return rankings


def rank_formulas_ml(
    features: dict,
    memory_items: list[dict],
    formula_names: list[str],
) -> list[FormulaRanking]:
    """Rank formulas using ML if enough data, with fallback chain.

    Fallback chain: ML model -> historical mean -> regime default.

    Each memory_item should have:
      - record.formula_name
      - record.trade_outcome
      - record with regime_label and optional numeric indicators

    Returns ranked list of FormulaRanking, best first.
    """
    # Detect current regime
    try:
        from shared.regime import detect_regime
        regime = detect_regime(features)
        regime_label = regime.label
    except Exception:
        regime_label = "unknown"

    # ---- Collect training data ----
    per_formula: dict[str, dict] = {}  # fname -> {X, y, outcomes}
    for item in memory_items:
        record = item.get("record", {})
        fname = record.get("formula_name")
        outcome = record.get("trade_outcome")
        if fname is None or outcome is None:
            continue

        regime_str = record.get("regime_label", "")
        feat_vec = _extract_numeric_regime_features(regime_str, record)
        rolling_perf = _compute_rolling_performance(memory_items, fname, window=10)
        full_features = feat_vec + [rolling_perf]

        if fname not in per_formula:
            per_formula[fname] = {"X": [], "y": [], "outcomes": []}
        per_formula[fname]["X"].append(full_features)
        per_formula[fname]["y"].append(float(outcome))
        per_formula[fname]["outcomes"].append(float(outcome))

    total_samples = sum(len(v["y"]) for v in per_formula.values())

    # ---- Try ML path ----
    if total_samples >= MIN_SAMPLES_FOR_ML:
        try:
            import numpy as np
            from sklearn.ensemble import RandomForestRegressor
            from sklearn.model_selection import TimeSeriesSplit

            # Current market feature vector
            current_regime_features = _extract_numeric_regime_features(regime_label, features)
            # Rolling perf placeholder (0.0 for prediction — not yet traded)
            current_features_base = current_regime_features

            rankings = []
            for fname in formula_names:
                fdata = per_formula.get(fname)
                if fdata is None or len(fdata["y"]) < MIN_SAMPLES_PER_FORMULA:
                    continue

                X_arr = np.array(fdata["X"])
                y_arr = np.array(fdata["y"])
                n_samples = len(y_arr)

                # Add current rolling performance for this formula
                rolling_perf = _compute_rolling_performance(memory_items, fname, window=10)
                current_features = np.array([current_features_base + [rolling_perf]])

                # Check if retrain needed
                data_hash = _compute_data_hash(n_samples, fdata["outcomes"])
                if not _needs_retrain(fname, data_hash, n_samples):
                    # Use cached model
                    cached = _model_cache[fname]
                    predicted = float(cached.model.predict(current_features)[0])
                    # Use cached confidence
                    rankings.append(FormulaRanking(
                        name=fname,
                        predicted_score=round(predicted, 6),
                        selection_method="ml_model",
                        confidence=round(min(n_samples / 200, 0.95), 3),
                    ))
                    continue

                # Train with 5-fold TimeSeriesSplit cross-validation
                n_splits = min(5, max(2, n_samples // 20))
                tscv = TimeSeriesSplit(n_splits=n_splits)

                fold_predictions = []
                best_model = None
                for train_idx, val_idx in tscv.split(X_arr):
                    if len(train_idx) < MIN_SAMPLES_PER_FORMULA:
                        continue
                    model = RandomForestRegressor(
                        n_estimators=100,
                        max_depth=5,
                        min_samples_leaf=3,
                        random_state=42,
                        n_jobs=-1,
                    )
                    model.fit(X_arr[train_idx], y_arr[train_idx])
                    fold_pred = float(model.predict(current_features)[0])
                    fold_predictions.append(fold_pred)
                    best_model = model  # keep last fold model (trained on most data)

                if not fold_predictions or best_model is None:
                    continue

                # Final model: train on all data
                final_model = RandomForestRegressor(
                    n_estimators=100,
                    max_depth=5,
                    min_samples_leaf=3,
                    random_state=42,
                    n_jobs=-1,
                )
                final_model.fit(X_arr, y_arr)

                # Cache it
                _model_cache[fname] = _CachedModel(
                    model=final_model,
                    data_hash=data_hash,
                    sample_count=n_samples,
                )

                predicted = float(final_model.predict(current_features)[0])

                # Confidence from prediction variance across CV folds + sample size
                pred_std = float(np.std(fold_predictions)) if len(fold_predictions) > 1 else 1.0
                stability = max(0.0, 1.0 - pred_std * 5)  # lower variance = higher confidence
                sample_conf = min(n_samples / 200, 1.0)
                confidence = stability * 0.5 + sample_conf * 0.5

                rankings.append(FormulaRanking(
                    name=fname,
                    predicted_score=round(predicted, 6),
                    selection_method="ml_model",
                    confidence=round(min(confidence, 0.95), 3),
                ))

            if rankings:
                rankings.sort(key=lambda r: r.predicted_score, reverse=True)
                logger.info(
                    "ml_formula_ranking",
                    extra={
                        "top_formula": rankings[0].name,
                        "n_formulas": len(rankings),
                        "n_samples": total_samples,
                        "method": "ml_model",
                    },
                )
                return rankings

        except Exception as exc:
            logger.warning("ml_formula_ranking_failed", extra={"error": str(exc)})

    # ---- Fallback: historical mean ----
    if total_samples > 0:
        rankings = _rank_by_historical_mean(memory_items, formula_names, regime_label)
        if rankings:
            logger.info(
                "ml_formula_ranking",
                extra={
                    "top_formula": rankings[0].name,
                    "n_formulas": len(rankings),
                    "n_samples": total_samples,
                    "method": "historical_mean",
                },
            )
            return rankings

    # ---- Last resort: regime default ----
    rankings = _rank_by_regime_default(formula_names, regime_label)
    logger.info(
        "ml_formula_ranking",
        extra={
            "top_formula": rankings[0].name if rankings else "none",
            "n_formulas": len(rankings),
            "n_samples": total_samples,
            "method": "regime_default",
        },
    )
    return rankings


def clear_model_cache() -> None:
    """Clear all cached models. Useful for testing or forced retraining."""
    _model_cache.clear()
