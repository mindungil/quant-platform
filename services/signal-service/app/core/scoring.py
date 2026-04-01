import math
from app.models.signal import ExternalContextSnapshot, FeatureSnapshot, SignalEvaluationResponse


def _normalize(value: float, lower: float, upper: float) -> float:
    span = upper - lower
    if span == 0:
        return 0.0
    centered = (value - lower) / span
    return max(0.0, min(1.0, centered))


def _tanh_normalize(value: float, scale: float) -> float:
    """Normalize a value to [-1, 1] using tanh with a scale factor."""
    if scale <= 0:
        return 0.0
    return math.tanh(value / scale)


def build_signal_response(
    asset: str,
    features: FeatureSnapshot,
    threshold: float,
    entry_threshold: float | None = None,
    exit_threshold: float | None = None,
    asset_type: str = "crypto",
    strategy_id: str | None = None,
    strategy_user_id: str | None = None,
    external_context: ExternalContextSnapshot | None = None,
    external_signal_weight: float = 0.0,
) -> SignalEvaluationResponse:
    score_components: dict[str, float] = {}
    technical_components: list[float] = []
    external_components: list[float] = []

    # ATR for normalization (fallback to 1% of price if unavailable)
    atr = features.atr_14 if features.atr_14 and features.atr_14 > 0 else (features.close * 0.01 if features.close else 1.0)

    # --- RSI: continuous confidence, not just direction ---
    # RSI deviation from neutral (50), normalized to [-1, 1]
    if features.rsi_14 is not None:
        rsi_confidence = (features.rsi_14 - 50) / 50  # [-1, 1]
        score_components["rsi"] = round(rsi_confidence, 4)
        technical_components.append(rsi_confidence)

    # --- MACD: histogram magnitude normalized by ATR ---
    if features.macd is not None and features.macd_signal is not None:
        macd_histogram = features.macd - features.macd_signal
        macd_confidence = _tanh_normalize(macd_histogram, atr)
        score_components["macd"] = round(macd_confidence, 4)
        technical_components.append(macd_confidence)

    # --- SMA_20: distance from SMA in ATR units ---
    if features.close is not None and features.sma_20 is not None:
        distance = features.close - features.sma_20
        sma_confidence = _tanh_normalize(distance, atr * math.sqrt(20))
        score_components["sma_20"] = round(sma_confidence, 4)
        technical_components.append(sma_confidence)

    # --- VWAP: distance from VWAP in ATR units ---
    if features.close is not None and features.vwap is not None:
        distance = features.close - features.vwap
        vwap_confidence = _tanh_normalize(distance, atr * 2)
        score_components["vwap"] = round(vwap_confidence, 4)
        technical_components.append(vwap_confidence)

    # --- Bollinger %B: position within bands mapped to [-1, 1] ---
    if features.bb_upper is not None and features.bb_lower is not None and features.close is not None:
        bb_range = features.bb_upper - features.bb_lower
        if bb_range > 0:
            bb_pct_b = (features.close - features.bb_lower) / bb_range  # [0, 1] normally
            bb_confidence = (bb_pct_b - 0.5) * 2  # map to [-1, 1]
            bb_confidence = max(-1.0, min(1.0, bb_confidence))
            score_components["bollinger"] = round(bb_confidence, 4)
            technical_components.append(bb_confidence)

    # --- Stochastic: deviation from 50, like RSI ---
    if features.stochastic_k is not None:
        stoch_confidence = (features.stochastic_k - 50) / 50
        score_components["stochastic"] = round(stoch_confidence, 4)
        technical_components.append(stoch_confidence)

    # --- ADX trend filter: scale down signals in ranging markets ---
    adx_multiplier = 1.0
    if features.adx_14 is not None:
        if features.adx_14 < 20:
            adx_multiplier = 0.5  # weak trend — reduce signal confidence
        elif features.adx_14 > 40:
            adx_multiplier = 1.2  # strong trend — boost confidence
        score_components["adx_filter"] = round(adx_multiplier, 2)

    # --- External context signals ---
    if external_context is not None:
        if external_context.news_sentiment is not None:
            score_components["news_sentiment"] = external_context.news_sentiment * external_signal_weight
            external_components.append(external_context.news_sentiment)
        if external_context.onchain_score is not None:
            score_components["onchain_score"] = external_context.onchain_score * external_signal_weight
            external_components.append(external_context.onchain_score)
        if external_context.macro_risk_score is not None:
            score_components["macro_risk_score"] = external_context.macro_risk_score * external_signal_weight
            external_components.append(external_context.macro_risk_score)
        if external_context.fear_greed_index is not None:
            fg_score = (external_context.fear_greed_index - 50) / 50
            score_components["fear_greed_index"] = fg_score * external_signal_weight
            external_components.append(fg_score)

    # --- Combine ---
    if not technical_components and not external_components:
        total_score = 0.0
    else:
        technical_score = sum(technical_components) / len(technical_components) if technical_components else 0.0
        # Apply ADX trend filter
        technical_score *= adx_multiplier

        if external_components:
            external_score = sum(external_components) / len(external_components)
            total_score = technical_score * (1 - external_signal_weight) + external_score * external_signal_weight
        else:
            total_score = technical_score

    # Clamp to [-1, 1]
    total_score = max(-1.0, min(1.0, total_score))

    positive_threshold = abs(entry_threshold if entry_threshold is not None else threshold)
    negative_threshold = abs(exit_threshold if exit_threshold is not None else threshold)
    threshold_crossed = total_score >= positive_threshold or total_score <= -negative_threshold
    direction = "BUY" if total_score >= positive_threshold else "SELL" if total_score <= -negative_threshold else "HOLD"
    effective_threshold = positive_threshold if total_score >= 0 else negative_threshold

    return SignalEvaluationResponse(
        asset=asset,
        asset_type=asset_type,
        strategy_id=strategy_id,
        strategy_user_id=strategy_user_id,
        signal_score=round(total_score, 4),
        threshold=effective_threshold,
        threshold_crossed=threshold_crossed,
        direction=direction,
        components=score_components,
        feature_timestamp=features.timestamp,
        external_timestamp=external_context.timestamp if external_context is not None else None,
        reference_price=features.close,
    )
