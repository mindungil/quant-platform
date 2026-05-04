import logging
import math
import os
from app.models.signal import ExternalContextSnapshot, FeatureSnapshot, SignalEvaluationResponse

from prometheus_client import Counter

logger = logging.getLogger(__name__)

# IC weight engine: data-driven factor weights from rolling IC analysis.
# Falls back to heuristic regime weights when insufficient IC data.
_ic_weights: dict[str, float] | None = None
_ic_weights_loaded = False
_ic_weights_loaded_at: float = 0.0
_IC_CACHE_TTL = 300.0  # Reload from Redis every 5 minutes

# Phase B: regime-adaptive weighting. Gated by env flag so we can ship
# the code in a disabled state and toggle per-environment after running
# the 8-year walk-forward backtest to confirm the Sharpe 1.35-1.54
# baseline is preserved or improved. Default OFF.
_REGIME_ADAPTIVE_ENABLED = os.getenv("SIGNAL_REGIME_ADAPTIVE", "false").lower() == "true"

try:
    signal_weight_mode_total = Counter(
        "signal_weight_mode_total",
        "Scoring paths taken per request (regime-adaptive vs aggregate vs heuristic)",
        ["mode"],
    )
except ValueError:
    from prometheus_client import REGISTRY
    signal_weight_mode_total = REGISTRY._names_to_collectors["signal_weight_mode_total"]


def _load_ic_weights() -> dict[str, float]:
    """Load IC-derived weights from shared engine (Redis-backed).

    Caches for 5 minutes to avoid Redis round-trips on every signal evaluation.
    The daily loop recomputes weights; this TTL ensures signal-service picks up
    new weights within one fast-loop cycle.
    """
    import time
    global _ic_weights, _ic_weights_loaded, _ic_weights_loaded_at

    now = time.monotonic()
    if _ic_weights_loaded and (now - _ic_weights_loaded_at) < _IC_CACHE_TTL:
        return _ic_weights or {}

    try:
        from shared.factors.ic_weight_engine import get_ic_engine
        engine = get_ic_engine()
        weights = engine.get_weights()
        if weights and len(weights) >= 3:
            _ic_weights = weights
            logger.info("ic_weights_loaded", extra={"n_factors": len(weights)})
        else:
            _ic_weights = None
    except Exception:
        _ic_weights = None

    _ic_weights_loaded = True
    _ic_weights_loaded_at = now
    return _ic_weights or {}


def _label_regime(features: FeatureSnapshot) -> str:
    """Map the current bar's features to a coarse regime label.

    Uses ADX for trend strength + ATR% for volatility. These fields are
    guaranteed by feature-store, unlike higher-order regime detectors
    that need a rolling window we don't keep at the scoring layer.

    Labels match `shared.regime.detector.VolTrendRegime.STATE_NAMES`
    so Phase B weights line up with the IC engine's shadow pool keys.
    """
    adx = features.adx_14 or 0.0
    atr = features.atr_14 or 0.0
    price = features.close or 0.0
    atr_pct = (atr / price) if (price and price > 0) else 0.0

    # CRISIS: vol far above typical crypto 1h ATR (~0.7-1.0%). 2.5%+ in an
    # hourly ATR is the liquidation-cascade zone.
    if atr_pct >= 0.025:
        return "CRISIS"
    # Trend strength via ADX; direction via MACD histogram fallback.
    if adx >= 25:
        macd_hist = 0.0
        if features.macd is not None and features.macd_signal is not None:
            macd_hist = features.macd - features.macd_signal
        return "TREND_UP" if macd_hist >= 0 else "TREND_DOWN"
    return "RANGE"


def _load_regime_weights(regime: str) -> dict[str, float] | None:
    """Attempt to load regime-conditional weights. Returns None if the
    engine is unavailable or the regime pool has insufficient data, in
    which case the caller falls back to the aggregate path."""
    if not _REGIME_ADAPTIVE_ENABLED:
        return None
    try:
        from shared.factors.ic_weight_engine import get_ic_engine
        engine = get_ic_engine()
        engine.get_weights()  # warm-load from Redis if not yet loaded
        return engine.get_regime_weights(regime)
    except Exception as exc:
        logger.debug("regime_weights_load_failed", extra={"regime": regime, "error": str(exc)[:100]})
        return None


def reload_ic_weights() -> None:
    """Force reload of IC weights (called by learning scheduler after recompute)."""
    global _ic_weights_loaded, _ic_weights_loaded_at
    _ic_weights_loaded = False
    _ic_weights_loaded_at = 0.0


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
    # Keyed technical components: (name, value) pairs for correct regime weighting
    technical_keyed: list[tuple[str, float]] = []
    external_components: list[float] = []

    MIN_TECHNICAL_COMPONENTS = 3  # Require at least 3 indicators for a meaningful signal

    # ATR for normalization (fallback to 1% of price if unavailable)
    atr = features.atr_14 if features.atr_14 and features.atr_14 > 0 else (features.close * 0.01 if features.close else 1.0)

    # --- RSI: deviation from neutral (50), normalized to [-1, 1] ---
    if features.rsi_14 is not None:
        rsi_confidence = (features.rsi_14 - 50) / 50
        score_components["rsi"] = round(rsi_confidence, 4)
        technical_keyed.append(("rsi", rsi_confidence))

    # --- MACD: histogram magnitude normalized by ATR ---
    if features.macd is not None and features.macd_signal is not None:
        macd_histogram = features.macd - features.macd_signal
        macd_confidence = _tanh_normalize(macd_histogram, atr)
        score_components["macd"] = round(macd_confidence, 4)
        technical_keyed.append(("macd", macd_confidence))

    # --- SMA_20: distance from SMA in ATR units ---
    if features.close is not None and features.sma_20 is not None:
        distance = features.close - features.sma_20
        sma_confidence = _tanh_normalize(distance, atr * math.sqrt(20))
        score_components["sma_20"] = round(sma_confidence, 4)
        technical_keyed.append(("sma_20", sma_confidence))

    # --- VWAP: distance from VWAP in ATR units ---
    if features.close is not None and features.vwap is not None:
        distance = features.close - features.vwap
        vwap_confidence = _tanh_normalize(distance, atr * 2)
        score_components["vwap"] = round(vwap_confidence, 4)
        technical_keyed.append(("vwap", vwap_confidence))

    # --- Bollinger %B: position within bands mapped to [-1, 1] ---
    if features.bb_upper is not None and features.bb_lower is not None and features.close is not None:
        bb_range = features.bb_upper - features.bb_lower
        if bb_range > 0:
            bb_pct_b = (features.close - features.bb_lower) / bb_range
            bb_confidence = (bb_pct_b - 0.5) * 2
            bb_confidence = max(-1.0, min(1.0, bb_confidence))
            score_components["bollinger"] = round(bb_confidence, 4)
            technical_keyed.append(("bollinger", bb_confidence))

    # --- Stochastic: deviation from 50 — EXCLUDED if RSI present (correlation > 0.85) ---
    if features.stochastic_k is not None and features.rsi_14 is None:
        stoch_confidence = (features.stochastic_k - 50) / 50
        score_components["stochastic"] = round(stoch_confidence, 4)
        technical_keyed.append(("stochastic", stoch_confidence))

    # --- ADX trend filter: scale down signals in ranging markets ---
    adx_multiplier = 1.0
    if features.adx_14 is not None:
        if features.adx_14 < 20:
            adx_multiplier = 0.5  # weak trend — reduce signal confidence
        elif features.adx_14 > 40:
            adx_multiplier = 1.2  # strong trend — boost confidence
        score_components["adx_filter"] = round(adx_multiplier, 2)

    # --- External context signals with differentiated weights ---
    # Fear/Greed and on-chain are more predictive for crypto than news sentiment.
    # Weights based on empirical IC research (Kaiko 2023, Santiment whitepapers).
    _EXT_WEIGHTS = {
        "fear_greed": 0.35,    # strong contrarian signal
        "onchain": 0.30,       # on-chain flows lead price
        "macro_risk": 0.20,    # macro backdrop
        "news_sentiment": 0.15, # noisy, lagging
    }
    external_keyed: list[tuple[str, float, float]] = []  # (name, value, weight)

    if external_context is not None:
        if external_context.news_sentiment is not None:
            w = _EXT_WEIGHTS["news_sentiment"]
            score_components["news_sentiment"] = round(external_context.news_sentiment * w, 4)
            external_keyed.append(("news_sentiment", external_context.news_sentiment, w))
        # On-chain composite is computed only for BTC (n_tx, hash_rate, fees
        # are BTC-specific; non-BTC assets get a hardcoded 0.0 in the
        # external-data-service snapshot). Including it for ETH/BNB/SOL would
        # add a constant -w bias from `(0 * 2 - 1) * w = -w` which is noise,
        # not signal. Skip for non-BTC assets.
        if external_context.onchain_score is not None and asset.upper().startswith("BTC"):
            # Normalize [0, 1] → [-1, 1]
            onchain_norm = external_context.onchain_score * 2 - 1
            w = _EXT_WEIGHTS["onchain"]
            score_components["onchain_score"] = round(onchain_norm * w, 4)
            external_keyed.append(("onchain", onchain_norm, w))
        if external_context.macro_risk_score is not None:
            # Normalize [0, 1] → [-1, 1] (higher risk = bearish)
            macro_norm = -(external_context.macro_risk_score * 2 - 1)
            w = _EXT_WEIGHTS["macro_risk"]
            score_components["macro_risk_score"] = round(macro_norm * w, 4)
            external_keyed.append(("macro_risk", macro_norm, w))
        if external_context.fear_greed_index is not None:
            fg_score = (external_context.fear_greed_index - 50) / 50
            w = _EXT_WEIGHTS["fear_greed"]
            score_components["fear_greed_index"] = round(fg_score * w, 4)
            external_keyed.append(("fear_greed", fg_score, w))

    external_components = [val for _, val, _ in external_keyed]

    # --- Minimum component gate ---
    technical_values = [v for _, v in technical_keyed]
    insufficient_data = len(technical_keyed) < MIN_TECHNICAL_COMPONENTS and not external_components
    score_components["_n_components"] = len(technical_keyed)

    if insufficient_data:
        total_score = 0.0
        score_components["_insufficient_data"] = 1.0
    elif not technical_keyed and not external_components:
        total_score = 0.0
    else:
        # Weight path selection (highest to lowest priority):
        #   1. regime-adaptive IC weights (if SIGNAL_REGIME_ADAPTIVE=true
        #      and the current regime has enough observations)
        #   2. aggregate IC weights (current production baseline)
        #   3. heuristic regime (trending vs reverting) — fallback only
        regime_label = _label_regime(features)
        # Encode regime as an int so the `components` dict stays
        # {str: float}-typed (the pydantic response model enforces that).
        # Mapping: 0=RANGE, 1=TREND_UP, 2=TREND_DOWN, 3=CRISIS.
        _REGIME_CODE = {"RANGE": 0.0, "TREND_UP": 1.0, "TREND_DOWN": 2.0, "CRISIS": 3.0}
        score_components["_regime_code"] = _REGIME_CODE.get(regime_label, 0.0)
        regime_weights = _load_regime_weights(regime_label)
        ic_weights = _load_ic_weights()

        use_regime = bool(regime_weights) and any(k in regime_weights for k, _ in technical_keyed)
        use_ic = (not use_regime) and bool(ic_weights) and any(k in ic_weights for k, _ in technical_keyed)

        adx = features.adx_14 or 20
        is_trending = adx >= 25

        momentum_names = {"rsi", "macd", "stochastic"}
        reversion_names = {"sma_20", "vwap", "bollinger"}

        weighted_sum = 0.0
        weight_total = 0.0

        if use_regime:
            # Regime-conditional IC weights (Phase B).
            score_components["_weight_mode"] = 2.0  # 2.0 = regime-adaptive
            signal_weight_mode_total.labels(mode="regime").inc()
            try:
                from shared.factors.ic_weight_engine import get_ic_engine
                inverted_map = get_ic_engine().get_regime_inverted(regime_label)
            except Exception:
                inverted_map = {}
            for key, val in technical_keyed:
                w_base = regime_weights.get(key, 0.0)
                if w_base <= 0:
                    w_base = 0.1
                if inverted_map.get(key):
                    val = -val
                w = w_base * (0.5 + abs(val) * 0.5)
                weighted_sum += val * w
                weight_total += w
        elif use_ic:
            # Aggregate IC weights (existing production path).
            score_components["_weight_mode"] = 1.0  # 1.0 = IC mode
            signal_weight_mode_total.labels(mode="aggregate").inc()
            for key, val in technical_keyed:
                ic_w = ic_weights.get(key, 0.0)
                if ic_w <= 0:
                    # Factor not in IC set or zero weight — use small baseline
                    ic_w = 0.1
                # Check if IC engine says this factor is inverted
                try:
                    from shared.factors.ic_weight_engine import get_ic_engine
                    if get_ic_engine().is_inverted(key):
                        val = -val  # flip inverted factors
                except Exception:
                    pass
                w = ic_w * (0.5 + abs(val) * 0.5)
                weighted_sum += val * w
                weight_total += w
        else:
            # Fallback: regime-based heuristic weighting
            score_components["_weight_mode"] = 0.0  # 0.0 = heuristic mode
            signal_weight_mode_total.labels(mode="heuristic").inc()
            for key, val in technical_keyed:
                w = 0.5 + abs(val) * 0.5
                if is_trending and key in momentum_names:
                    w *= 1.4
                elif is_trending and key in reversion_names:
                    w *= 0.7
                elif not is_trending and key in reversion_names:
                    w *= 1.4
                elif not is_trending and key in momentum_names:
                    w *= 0.7
                weighted_sum += val * w
                weight_total += w

        technical_score = weighted_sum / max(weight_total, 0.01)
        technical_score *= adx_multiplier

        # Signal agreement bonus: symmetric for both BUY and SELL directions
        if len(technical_keyed) >= 3:
            signs = [1 if v > 0.05 else (-1 if v < -0.05 else 0) for v in technical_values]
            pos = sum(1 for s in signs if s > 0)
            neg = sum(1 for s in signs if s < 0)
            agreement = max(pos, neg) / max(len(signs), 1)
            if agreement >= 0.8:
                technical_score *= 1.15
                score_components["_agreement_bonus"] = round(agreement, 2)

        if external_keyed:
            # Weighted average of external signals (not equal)
            ext_weighted_sum = sum(val * w for _, val, w in external_keyed)
            ext_weight_total = sum(w for _, _, w in external_keyed)
            external_score = ext_weighted_sum / max(ext_weight_total, 0.01)
            total_score = technical_score * (1 - external_signal_weight) + external_score * external_signal_weight
        else:
            total_score = technical_score

    # Clamp to [-1, 1]
    total_score = max(-1.0, min(1.0, total_score))

    positive_threshold = abs(entry_threshold if entry_threshold is not None else threshold)
    negative_threshold = abs(exit_threshold if exit_threshold is not None else threshold)

    if insufficient_data:
        threshold_crossed = False
        direction = "HOLD"
    else:
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
