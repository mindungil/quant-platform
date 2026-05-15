"""Strategy recommendation engine.

Analyzes market regime, available formulas, and historical performance
to recommend optimal strategies for the user.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from shared.regime import detect_regime, suggest_formula_type, MarketRegime
try:
    from shared.formulas import formula_registry
except ImportError:
    formula_registry = None  # type: ignore
try:
    import shared.formulas.momentum
    import shared.formulas.reversion
    import shared.formulas.breakout
    import shared.formulas.composite
except ImportError:
    pass  # formulas pkg is private — registry stays empty

logger = logging.getLogger("crypto-agent")


@dataclass
class StrategyRecommendation:
    name: str
    description: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    formula_name: str
    regime: str
    confidence: float
    reasoning: str


def _fetch_features(asset: str) -> dict:
    """Fetch latest features from feature-store via signal-service."""
    try:
        resp = httpx.post(
            f"{settings.signal_service_base_url}/signals/evaluate/{asset}",
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Extract feature values from signal components and raw fields
            features = {}
            for key in ("close", "volume", "rsi_14", "macd", "macd_signal",
                        "bb_upper", "bb_lower", "ema_9", "ema_21", "ema_50",
                        "sma_20", "atr_14", "adx_14", "stochastic_k", "stochastic_d", "vwap"):
                val = data.get(key) or data.get("components", {}).get(key)
                if val is not None:
                    try:
                        features[key] = float(val)
                    except (ValueError, TypeError):
                        pass
            return features
    except Exception as exc:
        logger.warning("feature_fetch_failed", extra={"error": str(exc), "asset": asset})
    return {}


def _fetch_memory_performance(regime_label: str, asset: str) -> dict[str, float]:
    """Query memory for formula performance in this regime."""
    try:
        resp = httpx.post(
            f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
            json={"regime_label": regime_label, "asset": asset, "top_k": 50},
            timeout=5.0,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            formula_outcomes: dict[str, list[float]] = {}
            for item in items:
                record = item.get("record", {})
                fname = record.get("formula_name")
                outcome = record.get("trade_outcome")
                if fname and outcome is not None:
                    formula_outcomes.setdefault(fname, []).append(outcome)
            return {
                fname: sum(outcomes) / len(outcomes)
                for fname, outcomes in formula_outcomes.items()
            }
    except Exception:
        pass
    return {}


def recommend_strategies(
    asset: str = "BTCUSDT",
    asset_type: str = "crypto",
    top_k: int = 3,
) -> list[StrategyRecommendation]:
    """Generate strategy recommendations based on current market conditions.

    Returns top_k recommended strategies ranked by expected performance.
    """
    features = _fetch_features(asset)
    regime = detect_regime(features)
    suggested_type = suggest_formula_type(regime)
    memory_perf = _fetch_memory_performance(regime.label, asset)

    recommendations: list[StrategyRecommendation] = []

    # Score each formula
    for formula in formula_registry.list_all():
        # Compute formula score on current features
        result = formula.compute(features)

        # Historical performance from memory
        historical_avg = memory_perf.get(formula.name, 0.0)

        # Regime match bonus
        regime_match = 1.0 if formula.best_regime == suggested_type or formula.best_regime == "any" else 0.3

        # Overall confidence
        confidence = (
            result.confidence * 0.4 +     # current signal strength
            min(abs(historical_avg) * 10, 1.0) * 0.3 +  # historical performance
            regime_match * 0.3              # regime compatibility
        )

        # Build indicator list and weights for this formula
        indicator_map = {
            "momentum_ema_cross": (["EMA"], {"EMA": 1.0}),
            "macd_histogram": (["MACD"], {"MACD": 1.0}),
            "stochastic_momentum": (["RSI", "MACD"], {"RSI": 0.5, "MACD": 0.5}),
            "mean_reversion_bb": (["RSI", "EMA"], {"RSI": 0.6, "EMA": 0.4}),
            "vwap_reversion": (["RSI", "MACD"], {"RSI": 0.5, "MACD": 0.5}),
            "rsi_divergence": (["RSI"], {"RSI": 1.0}),
            "volatility_breakout": (["MACD", "EMA"], {"MACD": 0.5, "EMA": 0.5}),
            "composite_adaptive": (["RSI", "MACD", "EMA"], {"RSI": 0.35, "MACD": 0.35, "EMA": 0.3}),
        }
        indicators, weights = indicator_map.get(formula.name, (["RSI", "MACD"], {"RSI": 0.5, "MACD": 0.5}))

        # Dynamic thresholds based on regime
        if regime.volatility == "high":
            entry, exit_t = 0.6, 0.4
        elif regime.volatility == "low":
            entry, exit_t = 0.3, 0.2
        else:
            entry, exit_t = 0.5, 0.3

        # Reasoning
        regime_kr = {
            "trending": "추세장", "sideways": "횡보장",
            "reversal": "반전장", "breakout": "돌파장", "any": "범용",
        }
        vol_kr = {"high": "고변동성", "normal": "보통", "low": "저변동성"}
        mom_kr = {"bullish": "상승", "bearish": "하락", "neutral": "중립"}

        regime_desc = f"{regime_kr.get(regime.trend_strength, regime.trend_strength)} / {vol_kr.get(regime.volatility, regime.volatility)} / {mom_kr.get(regime.momentum, regime.momentum)}"

        hist_text = ""
        if historical_avg > 0:
            hist_text = f" 과거 평균 수익률 +{historical_avg*100:.1f}%."
        elif historical_avg < 0:
            hist_text = f" 과거 평균 수익률 {historical_avg*100:.1f}%."

        reasoning = (
            f"현재 시장: {regime_desc}. "
            f"{formula.description}. "
            f"레짐 적합도: {'높음' if regime_match >= 0.8 else '보통' if regime_match >= 0.5 else '낮음'}."
            f"{hist_text}"
        )

        # Name in Korean
        name_map = {
            "momentum_ema_cross": "EMA 교차 모멘텀",
            "macd_histogram": "MACD 히스토그램 모멘텀",
            "stochastic_momentum": "스토캐스틱 모멘텀",
            "mean_reversion_bb": "볼린저 밴드 평균회귀",
            "vwap_reversion": "VWAP 평균회귀",
            "rsi_divergence": "RSI 다이버전스 반전",
            "volatility_breakout": "변동성 돌파",
            "composite_adaptive": "복합 적응형",
        }

        recommendations.append(StrategyRecommendation(
            name=name_map.get(formula.name, formula.name),
            description=reasoning,
            asset_type=asset_type,
            indicators=indicators,
            weights=weights,
            thresholds={"entry": entry, "exit": exit_t},
            formula_name=formula.name,
            regime=regime.label,
            confidence=round(confidence, 3),
            reasoning=reasoning,
        ))

    # Sort by confidence descending
    recommendations.sort(key=lambda r: r.confidence, reverse=True)
    return recommendations[:top_k]
