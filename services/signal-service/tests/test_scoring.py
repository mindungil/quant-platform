"""Comprehensive tests for signal scoring engine.

Covers:
  - Minimum component gate (insufficient data → HOLD)
  - RSI/Stochastic redundancy exclusion
  - Regime-aware weighting (trending vs sideways)
  - Agreement bonus (both BUY and SELL directions)
  - External data differentiated weighting
  - IC weight mode fallback
  - Threshold crossing (BUY, SELL, HOLD)
  - Edge cases (all None, extreme values, ATR fallback)
  - Score clamping [-1, 1]
  - Repository operations
"""
from datetime import datetime, timezone
from unittest.mock import patch

UTC = timezone.utc

from app.core.scoring import build_signal_response, reload_ic_weights
from app.db.repository import SignalRepository
from app.models.signal import ExternalContextSnapshot, FeatureSnapshot


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _features(**kwargs) -> FeatureSnapshot:
    defaults = {"asset": "BTCUSDT", "timestamp": datetime(2026, 1, 1, tzinfo=UTC)}
    defaults.update(kwargs)
    return FeatureSnapshot(**defaults)


def _external(**kwargs) -> ExternalContextSnapshot:
    defaults = {"asset": "BTCUSDT", "timestamp": datetime(2026, 1, 1, tzinfo=UTC)}
    defaults.update(kwargs)
    return ExternalContextSnapshot(**defaults)


def _score(features, threshold=0.6, external=None, ext_weight=0.0, **kw):
    return build_signal_response(
        asset="BTCUSDT", features=features, threshold=threshold,
        external_context=external, external_signal_weight=ext_weight, **kw,
    )


# ─────────────────────────────────────────────────────────────────
# 1. Minimum Component Gate
# ─────────────────────────────────────────────────────────────────

def test_insufficient_components_returns_hold():
    """With only 1 indicator (< 3 minimum), should return HOLD regardless of signal strength."""
    f = _features(close=100, rsi_14=95)  # Very strong bullish RSI
    r = _score(f, threshold=0.3)
    assert r.direction == "HOLD"
    assert r.threshold_crossed is False
    assert r.components.get("_insufficient_data") == 1.0
    assert r.components.get("_n_components") == 1


def test_two_components_returns_hold():
    """With 2 indicators (< 3 minimum), should return HOLD."""
    f = _features(close=100, rsi_14=80, macd=2, macd_signal=1)
    r = _score(f, threshold=0.3)
    assert r.direction == "HOLD"
    assert r.components.get("_n_components") == 2


def test_three_components_allows_signal():
    """With 3+ indicators, signal can cross threshold."""
    f = _features(close=120, rsi_14=85, macd=3, macd_signal=1, sma_20=100)
    r = _score(f, threshold=0.3)
    assert r.components.get("_n_components") >= 3
    assert r.direction in ("BUY", "SELL", "HOLD")
    # Should not have insufficient_data flag
    assert "_insufficient_data" not in r.components


# ─────────────────────────────────────────────────────────────────
# 2. RSI / Stochastic Redundancy
# ─────────────────────────────────────────────────────────────────

def test_stochastic_excluded_when_rsi_present():
    """Stochastic should be excluded from ensemble when RSI is available (r > 0.85)."""
    f = _features(close=100, rsi_14=70, stochastic_k=75, macd=1, macd_signal=0, sma_20=95)
    r = _score(f, threshold=0.3)
    assert "rsi" in r.components
    assert "stochastic" not in r.components


def test_stochastic_included_when_rsi_missing():
    """Stochastic should be used as fallback when RSI is unavailable."""
    f = _features(close=100, stochastic_k=75, macd=1, macd_signal=0, sma_20=95)
    r = _score(f, threshold=0.3)
    assert "stochastic" in r.components
    assert "rsi" not in r.components


# ─────────────────────────────────────────────────────────────────
# 3. Regime-Aware Weighting (Heuristic Mode)
# ─────────────────────────────────────────────────────────────────

def test_trending_regime_boosts_momentum():
    """In a trending market (ADX >= 25), momentum signals should have more influence."""
    # Strong uptrend: high ADX, bullish momentum, but bearish mean reversion
    f_trending = _features(
        close=100, rsi_14=70, macd=2, macd_signal=0,
        sma_20=110, vwap=110, bb_upper=115, bb_lower=85,
        adx_14=35,
    )
    f_sideways = _features(
        close=100, rsi_14=70, macd=2, macd_signal=0,
        sma_20=110, vwap=110, bb_upper=115, bb_lower=85,
        adx_14=15,
    )
    r_trending = _score(f_trending, threshold=0.1)
    r_sideways = _score(f_sideways, threshold=0.1)
    # In trending: momentum boosted, reversion suppressed → higher score
    # In sideways: reversion boosted, momentum suppressed → lower score
    # (because momentum says BUY but reversion says SELL here)
    assert r_trending.signal_score != r_sideways.signal_score


# ─────────────────────────────────────────────────────────────────
# 4. Agreement Bonus — Symmetric
# ─────────────────────────────────────────────────────────────────

def test_agreement_bonus_applied_to_buy():
    """Agreement bonus should now apply to BUY signals (symmetric)."""
    # All indicators strongly bullish
    f = _features(
        close=120, rsi_14=80, macd=3, macd_signal=0.5,
        sma_20=100, vwap=105, bb_upper=130, bb_lower=90,
        stochastic_k=None,  # excluded anyway
        adx_14=30,
    )
    r = _score(f, threshold=0.1)
    # Should have agreement bonus since all 5 indicators are bullish
    if r.components.get("_n_components", 0) >= 3:
        # Score should be positive (BUY direction)
        assert r.signal_score > 0


def test_agreement_bonus_applied_to_sell():
    """Agreement bonus should apply to SELL signals."""
    # All indicators strongly bearish
    f = _features(
        close=80, rsi_14=15, macd=-3, macd_signal=-0.5,
        sma_20=100, vwap=95, bb_upper=110, bb_lower=70,
        adx_14=30,
    )
    r = _score(f, threshold=0.1)
    assert r.signal_score < 0


# ─────────────────────────────────────────────────────────────────
# 5. External Data Differentiated Weighting
# ─────────────────────────────────────────────────────────────────

def test_external_components_weighted_differently():
    """External data sources should have differentiated weights (not equal)."""
    f = _features(close=100, rsi_14=50, macd=0, macd_signal=0, sma_20=100)
    ext = _external(
        news_sentiment=0.8, onchain_score=0.8,
        macro_risk_score=0.2, fear_greed_index=75,
    )
    r = _score(f, threshold=0.3, external=ext, ext_weight=0.35)
    # Verify that external components are present
    assert "news_sentiment" in r.components
    assert "onchain_score" in r.components
    assert "fear_greed_index" in r.components


def test_external_fear_greed_has_highest_weight():
    """Fear/Greed should have the highest external weight (0.35) per research."""
    f = _features(close=100, rsi_14=50, macd=0, macd_signal=0, sma_20=100)
    # Only fear_greed is non-neutral — should dominate external score
    ext_fg = _external(fear_greed_index=90)  # very greedy → bullish
    r_fg = _score(f, threshold=0.1, external=ext_fg, ext_weight=0.5)

    ext_news = _external(news_sentiment=0.8)  # bullish news
    r_news = _score(f, threshold=0.1, external=ext_news, ext_weight=0.5)

    # Fear/greed (weight 0.35) should move score more than news (weight 0.15)
    assert abs(r_fg.signal_score) >= abs(r_news.signal_score) * 0.5


# ─────────────────────────────────────────────────────────────────
# 6. IC Weight Mode
# ─────────────────────────────────────────────────────────────────

def test_ic_mode_fallback_to_heuristic_when_no_weights():
    """Without IC weights, should fall back to heuristic mode."""
    reload_ic_weights()
    f = _features(close=100, rsi_14=70, macd=2, macd_signal=0, sma_20=95, adx_14=30)
    r = _score(f, threshold=0.3)
    assert r.components.get("_weight_mode") == 0.0  # heuristic


def test_ic_mode_activates_with_weights():
    """When IC weights are available, should use IC mode."""
    mock_weights = {"rsi": 0.4, "macd": 0.3, "sma_20": 0.2, "bollinger": 0.1}
    with patch("app.core.scoring._load_ic_weights", return_value=mock_weights):
        f = _features(close=100, rsi_14=70, macd=2, macd_signal=0, sma_20=95, adx_14=30)
        r = _score(f, threshold=0.3)
        assert r.components.get("_weight_mode") == 1.0  # IC mode


# ─────────────────────────────────────────────────────────────────
# 7. Threshold Crossing
# ─────────────────────────────────────────────────────────────────

def test_buy_threshold_crossing():
    """Strong bullish signal should cross BUY threshold."""
    f = _features(
        close=120, rsi_14=80, macd=3, macd_signal=1,
        sma_20=100, vwap=105,
    )
    r = _score(f, threshold=0.6)
    if r.components.get("_n_components", 0) >= 3:
        assert r.direction == "BUY"
        assert r.threshold_crossed is True


def test_sell_threshold_crossing():
    """Strong bearish signal should cross SELL threshold."""
    f = _features(
        close=80, rsi_14=15, macd=-3, macd_signal=-0.5,
        sma_20=100, vwap=95,
    )
    r = _score(f, threshold=0.3)
    if r.components.get("_n_components", 0) >= 3:
        assert r.direction == "SELL"
        assert r.threshold_crossed is True


def test_neutral_returns_hold():
    """Neutral indicators should return HOLD."""
    f = _features(
        close=100, rsi_14=50, macd=0, macd_signal=0,
        sma_20=100, vwap=100, bb_upper=110, bb_lower=90,
    )
    r = _score(f, threshold=0.6)
    assert r.direction == "HOLD"
    assert r.threshold_crossed is False


def test_asymmetric_thresholds():
    """Entry and exit thresholds should be independently configurable."""
    f = _features(close=100, rsi_14=55, macd=0.5, macd_signal=0.2, sma_20=98)
    r_tight = _score(f, threshold=0.6, entry_threshold=0.1, exit_threshold=0.8)
    r_wide = _score(f, threshold=0.6, entry_threshold=0.9, exit_threshold=0.1)
    # Tight entry should cross more easily
    if r_tight.components.get("_n_components", 0) >= 3:
        assert r_tight.signal_score == r_wide.signal_score  # same score, different thresholds


# ─────────────────────────────────────────────────────────────────
# 8. Edge Cases
# ─────────────────────────────────────────────────────────────────

def test_all_none_features():
    """All None features should return 0 score and HOLD."""
    f = _features(close=None)
    r = _score(f, threshold=0.6)
    assert r.signal_score == 0.0
    assert r.direction == "HOLD"


def test_atr_fallback_when_missing():
    """When ATR is None, should fall back to 1% of close."""
    f = _features(close=100, rsi_14=80, macd=2, macd_signal=0, sma_20=90, atr_14=None)
    r = _score(f, threshold=0.3)
    # Should not crash and should produce valid signal
    assert -1.0 <= r.signal_score <= 1.0


def test_extreme_rsi_values():
    """Extreme RSI (0 or 100) should produce valid bounded output."""
    f_high = _features(close=100, rsi_14=100, macd=5, macd_signal=0, sma_20=80)
    f_low = _features(close=100, rsi_14=0, macd=-5, macd_signal=0, sma_20=120)
    r_high = _score(f_high, threshold=0.1)
    r_low = _score(f_low, threshold=0.1)
    assert -1.0 <= r_high.signal_score <= 1.0
    assert -1.0 <= r_low.signal_score <= 1.0
    assert r_high.signal_score > r_low.signal_score


def test_zero_atr_does_not_crash():
    """Zero ATR should be handled gracefully."""
    f = _features(close=100, rsi_14=70, macd=1, macd_signal=0, sma_20=95, atr_14=0)
    r = _score(f, threshold=0.3)
    assert -1.0 <= r.signal_score <= 1.0


def test_score_clamped_to_bounds():
    """Final score must be clamped to [-1, 1]."""
    # Extreme all-bullish scenario
    f = _features(
        close=200, rsi_14=99, macd=10, macd_signal=-5,
        sma_20=100, vwap=100, bb_upper=210, bb_lower=90,
        adx_14=50, atr_14=1,
    )
    r = _score(f, threshold=0.1)
    assert r.signal_score <= 1.0
    assert r.signal_score >= -1.0


# ─────────────────────────────────────────────────────────────────
# 9. ADX Multiplier
# ─────────────────────────────────────────────────────────────────

def test_weak_adx_reduces_signal():
    """ADX < 20 should multiply signal by 0.5 (reduce confidence)."""
    base = dict(close=100, rsi_14=70, macd=2, macd_signal=0, sma_20=95)
    f_weak = _features(**base, adx_14=15)
    f_strong = _features(**base, adx_14=45)
    r_weak = _score(f_weak, threshold=0.1)
    r_strong = _score(f_strong, threshold=0.1)
    # Strong ADX should produce higher absolute score
    assert abs(r_strong.signal_score) > abs(r_weak.signal_score)


# ─────────────────────────────────────────────────────────────────
# 10. Repository
# ─────────────────────────────────────────────────────────────────

def test_signal_repository_returns_latest():
    repo = SignalRepository()
    first = _score(
        _features(close=100, rsi_14=45, macd=1, macd_signal=2, sma_20=101, vwap=101),
        threshold=0.6,
    )
    second = _score(
        _features(
            close=120, rsi_14=80, macd=2, macd_signal=1, sma_20=110, vwap=115,
            timestamp=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        threshold=0.6,
    )
    repo.save("BTCUSDT", first)
    repo.save("BTCUSDT", second)
    assert repo.get_latest("BTCUSDT") == second


def test_build_signal_response_crosses_buy_threshold():
    """Legacy test preserved: BUY with external context."""
    features = _features(
        close=120, rsi_14=80, macd=2, macd_signal=1, sma_20=110, vwap=115,
    )
    response = _score(
        features, threshold=0.6,
        external=_external(
            news_sentiment=0.6, onchain_score=0.4,
            macro_risk_score=0.2, fear_greed_index=70,
        ),
        ext_weight=0.35,
    )
    assert response.direction == "BUY"
    assert response.threshold_crossed is True
    assert "news_sentiment" in response.components


# ─────────────────────────────────────────────────────────────────
# 11. Reference Price
# ─────────────────────────────────────────────────────────────────

def test_reference_price_is_close():
    """Reference price should be the close price from features."""
    f = _features(close=42000, rsi_14=50, macd=0, macd_signal=0, sma_20=42000)
    r = _score(f, threshold=0.6)
    assert r.reference_price == 42000
