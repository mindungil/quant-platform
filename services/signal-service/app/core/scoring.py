from app.models.signal import ExternalContextSnapshot, FeatureSnapshot, SignalEvaluationResponse


def _normalize(value: float, lower: float, upper: float) -> float:
    span = upper - lower
    if span == 0:
        return 0.0
    centered = (value - lower) / span
    return max(0.0, min(1.0, centered))


def build_signal_response(
    asset: str,
    features: FeatureSnapshot,
    threshold: float,
    external_context: ExternalContextSnapshot | None = None,
    external_signal_weight: float = 0.0,
) -> SignalEvaluationResponse:
    score_components: dict[str, float] = {}

    if features.rsi_14 is not None:
        score_components["rsi"] = (_normalize(features.rsi_14, 0, 100) - 0.5) * 2

    if features.macd is not None and features.macd_signal is not None:
        score_components["macd"] = 1.0 if features.macd > features.macd_signal else -1.0

    if features.close is not None and features.sma_20 is not None:
        score_components["sma_20"] = 1.0 if features.close > features.sma_20 else -1.0

    if features.close is not None and features.vwap is not None:
        score_components["vwap"] = 1.0 if features.close > features.vwap else -1.0

    if external_context is not None:
        if external_context.news_sentiment is not None:
            score_components["news_sentiment"] = external_context.news_sentiment * external_signal_weight
        if external_context.onchain_score is not None:
            score_components["onchain_score"] = external_context.onchain_score * external_signal_weight
        if external_context.macro_risk_score is not None:
            score_components["macro_risk_score"] = external_context.macro_risk_score * external_signal_weight
        if external_context.fear_greed_index is not None:
            score_components["fear_greed_index"] = (
                ((external_context.fear_greed_index - 50) / 50) * external_signal_weight
            )

    if not score_components:
        total_score = 0.0
    else:
        total_score = sum(score_components.values()) / len(score_components)

    threshold_crossed = abs(total_score) >= threshold
    direction = "BUY" if total_score >= threshold else "SELL" if total_score <= -threshold else "HOLD"

    return SignalEvaluationResponse(
        asset=asset,
        signal_score=round(total_score, 4),
        threshold=threshold,
        threshold_crossed=threshold_crossed,
        direction=direction,
        components=score_components,
        feature_timestamp=features.timestamp,
        external_timestamp=external_context.timestamp if external_context is not None else None,
    )
