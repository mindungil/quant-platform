"""Event Impact Prediction Model.

Instead of simple sentiment [-1, +1], predicts the EXPECTED PRICE IMPACT
of a news event as an unbounded z-score. This captures:

  1. Direction: bullish or bearish
  2. Magnitude: "BTC taxed 50%" >> "BTC drifts sideways"
  3. Novelty: first-time events get higher impact

Architecture:
  - Feature extraction: CryptoBERT embedding + severity keywords + volume surge + source weight
  - Model: LightGBM regression (CPU-friendly, trains in seconds)
  - Target: z-score of forward price return (1h, 6h, 24h)
  - Online learning: retrains weekly as new events accumulate

The output is UNBOUNDED — a major regulatory ban might score -8.0 while
normal news scores ±0.5. The alpha layer normalizes as needed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("impact-model")

_MODEL_DIR = Path(__file__).resolve().parents[4] / "data" / "models"
_MODEL_PATH = _MODEL_DIR / "impact_lgbm.pkl"
_FEATURES_PATH = _MODEL_DIR / "impact_features.json"

# ═══════════════════════════════════════════════════════════════
# Severity Keywords — events that historically cause large moves
# Weight: 1.0 = normal, 2.0 = significant, 3.0 = extreme
# ═══════════════════════════════════════════════════════════════

SEVERITY_KEYWORDS = {
    # Regulatory — extreme
    "ban": 3.0, "prohibition": 3.0, "illegal": 3.0, "crackdown": 2.5,
    "regulation": 2.0, "sec": 2.0, "lawsuit": 2.0, "enforcement": 2.0,
    "tax": 2.5, "compliance": 1.5, "sanction": 2.5, "cbdc": 1.5,
    # Market structure — extreme
    "etf approval": 3.0, "etf rejected": 3.0, "etf": 2.5,
    "halving": 2.5, "fork": 2.0, "upgrade": 1.5,
    # Security — extreme
    "hack": 3.0, "exploit": 3.0, "stolen": 3.0, "vulnerability": 2.5,
    "rug pull": 3.0, "scam": 2.0, "fraud": 2.5,
    # Adoption — significant
    "institutional": 2.0, "adoption": 2.0, "partnership": 1.5,
    "tesla": 2.0, "microstrategy": 2.0, "blackrock": 2.5,
    "country": 2.0, "legal tender": 3.0, "reserve": 2.5,
    # Macro — significant
    "fed": 2.0, "rate cut": 2.0, "rate hike": 2.0, "inflation": 1.5,
    "recession": 2.0, "war": 2.5, "default": 2.5,
    # Market events — extreme
    "crash": 3.0, "all-time high": 2.5, "record": 2.0,
    "liquidation": 2.5, "billion": 2.0, "trillion": 2.5,
    "surge": 1.5, "plunge": 2.0, "collapse": 3.0,
    # Korean
    "금지": 3.0, "규제": 2.0, "해킹": 3.0, "승인": 2.5,
    "폭락": 3.0, "급등": 2.0, "사기": 2.5, "제도화": 2.0,
}

# Source credibility weights
SOURCE_WEIGHTS = {
    "bloomberg": 3.0, "reuters": 3.0, "wsj": 2.5,
    "coindesk": 2.0, "cointelegraph": 2.0, "theblock": 2.0,
    "decrypt": 1.5, "cryptopanic": 1.0, "reddit": 0.8,
    "stocktwits": 0.7, "twitter": 0.6,
}


def extract_severity(text: str) -> float:
    """Compute severity score from keywords. Higher = more impactful."""
    lower = text.lower()
    scores = []
    for keyword, weight in SEVERITY_KEYWORDS.items():
        if keyword in lower:
            scores.append(weight)
    return max(scores) if scores else 1.0


def extract_source_weight(source: str) -> float:
    """Source credibility weight."""
    lower = source.lower()
    for key, weight in SOURCE_WEIGHTS.items():
        if key in lower:
            return weight
    return 1.0


def extract_features(
    nlp_score: float,
    nlp_confidence: float,
    severity: float,
    source_weight: float,
    volume_zscore: float,
    hour_of_day: int,
    day_of_week: int,
    fng_value: float | None = None,
) -> np.ndarray:
    """Extract feature vector for the impact model.

    Returns a 1D numpy array of 10 features.
    """
    return np.array([
        nlp_score,                          # 0: CryptoBERT direction
        nlp_confidence,                     # 1: model confidence
        nlp_score * severity,               # 2: direction × magnitude interaction
        severity,                           # 3: keyword severity
        source_weight,                      # 4: source credibility
        volume_zscore,                      # 5: news volume surprise
        nlp_score * volume_zscore,          # 6: direction × novelty interaction
        hour_of_day / 24.0,                 # 7: time of day (crypto has patterns)
        day_of_week / 7.0,                  # 8: day of week
        (fng_value or 50) / 100.0,          # 9: fear & greed context
    ], dtype=np.float32)


class ImpactModel:
    """LightGBM-based event impact predictor.

    Predicts the z-score of forward price return after a news event.
    Output is unbounded: ±0.5 = normal, ±2.0 = significant, ±5.0+ = extreme.
    """

    def __init__(self) -> None:
        self._model = None
        self._feature_names = [
            "nlp_score", "nlp_confidence", "direction_x_severity",
            "severity", "source_weight", "volume_zscore",
            "direction_x_novelty", "hour_norm", "dow_norm", "fng_norm",
        ]
        self._training_data: list[tuple[np.ndarray, float]] = []
        self._min_training_samples = 200
        self._load_model()

    def _load_model(self) -> None:
        """Load saved model if exists."""
        if _MODEL_PATH.exists():
            try:
                with open(_MODEL_PATH, "rb") as f:
                    self._model = pickle.load(f)
                logger.info("loaded impact model from %s", _MODEL_PATH)
            except Exception as e:
                logger.warning("model load failed: %s", e)

    def _save_model(self) -> None:
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(self._model, f)
        logger.info("saved impact model to %s", _MODEL_PATH)

    def predict(
        self,
        nlp_score: float,
        nlp_confidence: float,
        text: str,
        source: str = "",
        volume_zscore: float = 0.0,
        fng_value: float | None = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Predict price impact of a news event.

        Returns:
            impact: float — unbounded expected price impact (z-score)
            severity: float — keyword severity (1.0-3.0)
            confidence: float — prediction confidence
            method: str — 'lgbm' or 'heuristic'
        """
        ts = timestamp or datetime.now(timezone.utc)
        severity = extract_severity(text)
        src_weight = extract_source_weight(source)

        features = extract_features(
            nlp_score, nlp_confidence, severity, src_weight,
            volume_zscore, ts.hour, ts.weekday(), fng_value,
        )

        if self._model is not None:
            try:
                impact = float(self._model.predict(features.reshape(1, -1))[0])
                return {
                    "impact": round(impact, 4),
                    "severity": severity,
                    "confidence": round(min(nlp_confidence * src_weight / 3.0, 1.0), 4),
                    "method": "lgbm",
                }
            except Exception as e:
                logger.debug("lgbm predict failed: %s", e)

        # Heuristic fallback: direction × severity × source × novelty
        impact = nlp_score * severity * (0.5 + 0.5 * src_weight / 3.0) * (1.0 + volume_zscore * 0.3)
        return {
            "impact": round(impact, 4),
            "severity": severity,
            "confidence": round(0.3 * nlp_confidence, 4),
            "method": "heuristic",
        }

    def add_training_sample(self, features: np.ndarray, actual_return_zscore: float) -> None:
        """Add a realized event-return pair for future training."""
        self._training_data.append((features, actual_return_zscore))

    def train(self, X: np.ndarray | None = None, y: np.ndarray | None = None) -> dict:
        """Train or retrain the model.

        If X, y not provided, uses accumulated training data.
        Returns training metrics.
        """
        if X is None or y is None:
            if len(self._training_data) < self._min_training_samples:
                return {"status": "insufficient_data", "n_samples": len(self._training_data)}
            X = np.array([t[0] for t in self._training_data])
            y = np.array([t[1] for t in self._training_data])

        try:
            from lightgbm import LGBMRegressor

            model = LGBMRegressor(
                n_estimators=100,
                max_depth=5,
                learning_rate=0.1,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.1,
                reg_lambda=0.1,
                random_state=42,
                verbosity=-1,
                n_jobs=2,  # CPU-friendly
            )
            model.fit(X, y)

            # Evaluate on training set (simple — proper CV should be used for validation)
            pred = model.predict(X)
            mse = float(np.mean((pred - y) ** 2))
            corr = float(np.corrcoef(pred, y)[0, 1]) if len(y) > 2 else 0.0
            ic = corr  # information coefficient

            self._model = model
            self._save_model()

            logger.info("trained impact model: n=%d, MSE=%.4f, IC=%.4f", len(y), mse, ic)
            return {
                "status": "trained",
                "n_samples": len(y),
                "mse": round(mse, 4),
                "ic": round(ic, 4),
                "feature_importance": dict(zip(
                    self._feature_names,
                    [round(float(v), 4) for v in model.feature_importances_],
                )),
            }

        except ImportError:
            logger.warning("lightgbm not installed — cannot train")
            return {"status": "lightgbm_not_available"}
        except Exception as e:
            logger.exception("training failed")
            return {"status": "error", "error": str(e)[:200]}

    def build_training_data_from_history(
        self,
        sentiment_items: list[dict],
        price_df: pd.DataFrame,
        forward_hours: int = 6,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build training dataset from historical sentiment items + price data.

        For each news item, compute the actual forward return and use it as the label.
        This is how the model LEARNS what events actually moved the market.
        """
        close = price_df["close"]
        returns = close.pct_change()
        vol = returns.rolling(168).std()  # 7-day rolling vol

        X_list, y_list = [], []

        # Volume baseline for z-score
        hourly_counts: dict[str, int] = {}
        for item in sentiment_items:
            ts = pd.Timestamp(item["timestamp"])
            hour_key = ts.strftime("%Y-%m-%d-%H")
            hourly_counts[hour_key] = hourly_counts.get(hour_key, 0) + 1
        count_values = list(hourly_counts.values())
        count_mean = np.mean(count_values) if count_values else 5
        count_std = np.std(count_values) if len(count_values) > 1 else 1

        for item in sentiment_items:
            nlp_score = item.get("nlp_score")
            if nlp_score is None:
                continue

            ts = pd.Timestamp(item["timestamp"])
            if ts.tz is not None:
                ts = ts.tz_localize(None)

            # Find nearest price bar
            try:
                idx = close.index.get_indexer([ts], method="nearest")[0]
            except Exception:
                continue

            if idx < 0 or idx + forward_hours >= len(close):
                continue

            # Forward return z-score (this is the LABEL)
            fwd_ret = (close.iloc[idx + forward_hours] - close.iloc[idx]) / close.iloc[idx]
            current_vol = vol.iloc[idx]
            if current_vol is None or current_vol <= 0 or np.isnan(current_vol):
                continue
            fwd_zscore = fwd_ret / current_vol  # normalize by recent vol

            # Volume z-score for this hour
            hour_key = ts.strftime("%Y-%m-%d-%H")
            vol_z = (hourly_counts.get(hour_key, 0) - count_mean) / max(count_std, 0.1)

            features = extract_features(
                nlp_score=nlp_score,
                nlp_confidence=item.get("nlp_confidence", 0.5),
                severity=extract_severity(item.get("title", "")),
                source_weight=extract_source_weight(item.get("source", "")),
                volume_zscore=vol_z,
                hour_of_day=ts.hour,
                day_of_week=ts.weekday(),
                fng_value=item.get("fng_value"),
            )

            X_list.append(features)
            y_list.append(fwd_zscore)

        if not X_list:
            return np.array([]), np.array([])

        return np.array(X_list), np.array(y_list)

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @property
    def n_training_samples(self) -> int:
        return len(self._training_data)


# Singleton
impact_model = ImpactModel()
