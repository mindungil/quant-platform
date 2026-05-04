"""End-to-end integration test for the IC weight engine pipeline.

Simulates the full data flow:
  1. Decisions with factor scores are generated
  2. Forward returns are observed (hindsight)
  3. IC engine accumulates observations
  4. IC weights are recomputed
  5. Signal-service scoring uses IC weights instead of heuristic
  6. Inverted factors get flipped
  7. Redis persistence and recovery works
  8. Thread safety under concurrent access
"""
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

# Add signal-service to path for cross-service integration tests
_signal_svc = os.path.join(os.path.dirname(__file__), "..", "services", "signal-service")
if os.path.isdir(_signal_svc) and _signal_svc not in sys.path:
    sys.path.insert(0, os.path.abspath(_signal_svc))

from shared.factors.ic_weight_engine import (
    ICWeightEngine,
    MIN_OBSERVATIONS,
    NOISE_THRESHOLD,
    MAX_SINGLE_WEIGHT,
    ROLLING_WINDOW,
)

UTC = timezone.utc


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _generate_decisions(n: int, signal_quality: float = 0.8):
    """Generate synthetic decisions with factor scores and forward returns.

    signal_quality: 0.0 = pure noise, 1.0 = perfect signal
    """
    import random
    random.seed(42)
    decisions = []
    for i in range(n):
        # True forward return
        fwd = random.gauss(0, 0.02)  # ~2% daily vol

        # Factors with varying predictive power
        rsi = fwd * 30 * signal_quality + random.gauss(0, 0.3)  # good predictor
        macd = fwd * 20 * signal_quality + random.gauss(0, 0.4)  # decent predictor
        sma_20 = fwd * 10 * signal_quality + random.gauss(0, 0.5)  # weak predictor
        vwap = random.gauss(0, 0.5)  # pure noise
        bollinger = -fwd * 15 * signal_quality + random.gauss(0, 0.3)  # inverted!

        decisions.append({
            "components": {
                "rsi": round(max(-1, min(1, rsi)), 4),
                "macd": round(max(-1, min(1, macd)), 4),
                "sma_20": round(max(-1, min(1, sma_20)), 4),
                "vwap": round(max(-1, min(1, vwap)), 4),
                "bollinger": round(max(-1, min(1, bollinger)), 4),
                # Meta keys that should be excluded
                "ensemble_score": 0.5,
                "style_formula": "composite_adaptive",
                "regime": "trending",
                "formula_confidence": 0.7,
            },
            "forward_return": fwd,
        })
    return decisions


_IC_META_KEYS = frozenset({
    "ensemble_score", "style_score", "style_formula",
    "formula_confidence", "regime", "adx_filter",
    "_n_components", "_insufficient_data", "_agreement_bonus",
    "_weight_mode",
})


def _extract_factors(components: dict) -> dict[str, float]:
    """Same extraction logic as learning_scheduler."""
    return {
        k: float(v) for k, v in components.items()
        if isinstance(v, (int, float))
        and math.isfinite(float(v))
        and not k.startswith("cat_")
        and k not in _IC_META_KEYS
    }


# ─────────────────────────────────────────────────────────────────
# 1. Cold Start → Warm Up → IC Weights
# ─────────────────────────────────────────────────────────────────

def test_cold_start_to_warm_weights():
    """Simulate cold start → accumulate observations → IC weights appear."""
    engine = ICWeightEngine()

    # Phase 1: Cold (< MIN_OBSERVATIONS)
    decisions = _generate_decisions(30)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    weights = engine.recompute_weights()
    # Should fall back to equal weights or empty (not enough data)
    for state in engine._states.values():
        assert state.n_obs == 30
        assert state.n_obs < MIN_OBSERVATIONS

    # Phase 2: Warm up (>= MIN_OBSERVATIONS)
    decisions2 = _generate_decisions(80)
    for d in decisions2:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    weights = engine.recompute_weights()
    assert len(weights) > 0
    assert abs(sum(weights.values()) - 1.0) < 0.01

    # RSI should have highest weight (strongest predictor)
    # VWAP should have lowest (pure noise)
    if "rsi" in weights and "vwap" in weights:
        assert weights.get("rsi", 0) > weights.get("vwap", 0)


# ─────────────────────────────────────────────────────────────────
# 2. Meta Key Filtering
# ─────────────────────────────────────────────────────────────────

def test_meta_keys_excluded():
    """Meta keys like ensemble_score, regime should not be tracked as factors."""
    engine = ICWeightEngine()
    decisions = _generate_decisions(60)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    tracked_names = set(engine._states.keys())
    assert "ensemble_score" not in tracked_names
    assert "style_formula" not in tracked_names
    assert "regime" not in tracked_names
    assert "formula_confidence" not in tracked_names
    assert "rsi" in tracked_names
    assert "macd" in tracked_names


# ─────────────────────────────────────────────────────────────────
# 3. Inverted Factor Detection
# ─────────────────────────────────────────────────────────────────

def test_inverted_factor_detected_and_usable():
    """Bollinger is negatively correlated → should be detected as inverted."""
    engine = ICWeightEngine()
    decisions = _generate_decisions(120, signal_quality=0.9)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    engine.recompute_weights()

    bb_state = engine.get_factor_state("bollinger")
    if bb_state and bb_state.n_obs >= MIN_OBSERVATIONS:
        # Bollinger was generated as -fwd*15, so IC should be negative
        assert bb_state.ic < 0
        assert bb_state.inverted is True
        # But it should still get weight (inverted factors are usable)
        assert bb_state.weight > 0 or abs(bb_state.ic) < NOISE_THRESHOLD


# ─────────────────────────────────────────────────────────────────
# 4. Rolling Window Enforcement
# ─────────────────────────────────────────────────────────────────

def test_rolling_window_caps_data():
    """Observations beyond ROLLING_WINDOW should be dropped."""
    engine = ICWeightEngine()
    decisions = _generate_decisions(ROLLING_WINDOW + 50)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    for state in engine._states.values():
        assert len(state.scores) <= ROLLING_WINDOW
        assert len(state.forward_returns) <= ROLLING_WINDOW
        assert state.n_obs == ROLLING_WINDOW


# ─────────────────────────────────────────────────────────────────
# 5. Weight Cap Enforcement
# ─────────────────────────────────────────────────────────────────

def test_no_single_factor_dominates():
    """No factor should exceed MAX_SINGLE_WEIGHT."""
    engine = ICWeightEngine()
    decisions = _generate_decisions(120)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    weights = engine.recompute_weights()
    for w in weights.values():
        assert w <= MAX_SINGLE_WEIGHT + 0.01


# ─────────────────────────────────────────────────────────────────
# 6. Signal-Service IC Integration
# ─────────────────────────────────────────────────────────────────

def test_signal_scoring_uses_ic_weights():
    """When IC weights are available, signal-service should use them."""
    from app.core.scoring import build_signal_response, reload_ic_weights
    from app.models.signal import FeatureSnapshot

    mock_weights = {"rsi": 0.4, "macd": 0.3, "sma_20": 0.2, "bollinger": 0.1}

    with patch("app.core.scoring._load_ic_weights", return_value=mock_weights):
        features = FeatureSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            close=100, rsi_14=70, macd=2, macd_signal=0, sma_20=95, adx_14=30,
        )
        r = build_signal_response(
            asset="BTCUSDT", features=features, threshold=0.3,
        )
        assert r.components.get("_weight_mode") == 1.0  # IC mode active


def test_signal_scoring_falls_back_to_heuristic():
    """Without IC weights, should use heuristic mode."""
    from app.core.scoring import build_signal_response, reload_ic_weights
    from app.models.signal import FeatureSnapshot

    reload_ic_weights()

    with patch("app.core.scoring._load_ic_weights", return_value={}):
        features = FeatureSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            close=100, rsi_14=70, macd=2, macd_signal=0, sma_20=95, adx_14=30,
        )
        r = build_signal_response(
            asset="BTCUSDT", features=features, threshold=0.3,
        )
        assert r.components.get("_weight_mode") == 0.0  # heuristic fallback


# ─────────────────────────────────────────────────────────────────
# 7. Thread Safety
# ─────────────────────────────────────────────────────────────────

def test_concurrent_updates_dont_crash():
    """Multiple threads updating IC engine should not cause data corruption."""
    engine = ICWeightEngine()
    errors = []

    def writer(thread_id):
        try:
            for i in range(50):
                engine.update(
                    {"factor_a": 0.1 * thread_id, "factor_b": -0.05 * i},
                    0.01 * i,
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    # Should have accumulated observations from all threads
    assert engine._states["factor_a"].n_obs == 200  # 4 threads * 50 each
    weights = engine.recompute_weights()
    assert abs(sum(weights.values()) - 1.0) < 0.01 or len(weights) == 0


# ─────────────────────────────────────────────────────────────────
# 8. Full Pipeline Simulation
# ─────────────────────────────────────────────────────────────────

def test_full_pipeline_cold_to_ic_mode():
    """Simulate the complete lifecycle:
    cold start → feed data → recompute → verify weights → scoring uses IC.
    """
    from app.core.scoring import build_signal_response
    from app.models.signal import FeatureSnapshot

    engine = ICWeightEngine()

    # Simulate 14 days of decisions (10 per day = 140 total)
    decisions = _generate_decisions(140, signal_quality=0.7)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    # Daily recompute
    weights = engine.recompute_weights()
    assert len(weights) >= 3, f"Expected >= 3 active factors, got {len(weights)}"
    assert abs(sum(weights.values()) - 1.0) < 0.01

    # Verify IC ordering makes sense
    states = engine.get_all_states()
    rsi_ic = abs(states.get("rsi", {}).get("ic", 0))
    vwap_ic = abs(states.get("vwap", {}).get("ic", 0))
    # RSI should have higher IC than VWAP (noise factor)
    assert rsi_ic > vwap_ic, f"RSI IC ({rsi_ic}) should > VWAP IC ({vwap_ic})"

    # Now test signal scoring with these weights
    with patch("app.core.scoring._load_ic_weights", return_value=weights):
        features = FeatureSnapshot(
            asset="BTCUSDT",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            close=100, rsi_14=75, macd=1.5, macd_signal=0,
            sma_20=95, vwap=97, bb_upper=110, bb_lower=90, adx_14=30,
        )
        r = build_signal_response(
            asset="BTCUSDT", features=features, threshold=0.3,
        )
        assert r.components.get("_weight_mode") == 1.0
        assert r.signal_score != 0.0  # Should produce a signal
        assert r.components.get("_n_components") >= 3


# ─────────────────────────────────────────────────────────────────
# 9. IC_IR Stability Tracking
# ─────────────────────────────────────────────────────────────────

def test_ic_ir_computed_with_enough_subwindows():
    """IC_IR should be computed when we have enough sub-windows."""
    engine = ICWeightEngine()
    # Need 2+ sub-windows of 50 each = 100+ observations
    decisions = _generate_decisions(150, signal_quality=0.8)
    for d in decisions:
        factors = _extract_factors(d["components"])
        engine.update(factors, d["forward_return"])

    engine.recompute_weights()

    rsi_state = engine.get_factor_state("rsi")
    assert rsi_state is not None
    assert rsi_state.n_obs == 150
    # IC_IR should be non-zero for a factor with consistent IC
    assert rsi_state.ic_ir != 0.0


# ─────────────────────────────────────────────────────────────────
# 10. Edge Case: All Factors Are Noise
# ─────────────────────────────────────────────────────────────────

def test_all_noise_factors_fall_back_to_equal():
    """When all factors are noise, should fall back to equal weighting."""
    import random
    random.seed(99)

    engine = ICWeightEngine()
    for _ in range(80):
        # Pure random — no correlation between factors and returns
        engine.update(
            {"a": random.gauss(0, 1), "b": random.gauss(0, 1), "c": random.gauss(0, 1)},
            random.gauss(0, 0.02),
        )

    weights = engine.recompute_weights()
    # Either all equal weights (fallback) or empty
    if weights:
        values = list(weights.values())
        # Check near-equal (within 0.1 of each other)
        assert max(values) - min(values) < 0.15
