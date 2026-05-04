"""Tests for IC-based factor weight engine."""
import math
from shared.factors.ic_weight_engine import ICWeightEngine, MIN_OBSERVATIONS


def test_insufficient_data_returns_empty_weights():
    engine = ICWeightEngine()
    for i in range(MIN_OBSERVATIONS - 1):
        engine.update({"rsi": 0.5 + i * 0.01, "macd": -0.3}, 0.01 * i)
    weights = engine.recompute_weights()
    assert not weights or all(v == 0.0 for v in weights.values())


def test_sufficient_data_produces_weights():
    engine = ICWeightEngine()
    # Create factors with clear predictive signal
    for i in range(80):
        fwd = 0.01 * (i % 10 - 5)
        engine.update({
            "good_factor": fwd * 10 + 0.1,  # correlated with forward returns
            "noise_factor": (-1) ** i * 0.5,  # random noise
            "another_good": fwd * 5,
        }, fwd)
    weights = engine.recompute_weights()
    assert len(weights) > 0
    assert abs(sum(weights.values()) - 1.0) < 0.01


def test_weights_sum_to_one():
    engine = ICWeightEngine()
    for i in range(100):
        engine.update({
            "a": math.sin(i / 10),
            "b": math.cos(i / 10),
            "c": math.sin(i / 5),
        }, math.sin(i / 10) * 0.01)
    weights = engine.recompute_weights()
    if weights:
        assert abs(sum(weights.values()) - 1.0) < 0.01


def test_max_single_weight_cap():
    engine = ICWeightEngine()
    # One dominant factor, others weak
    for i in range(100):
        fwd = 0.01 * i
        engine.update({
            "dominant": fwd * 100,
            "weak1": 0.001 * i,
            "weak2": 0.002 * i,
        }, fwd)
    weights = engine.recompute_weights()
    if weights:
        for w in weights.values():
            assert w <= 0.36  # MAX_SINGLE_WEIGHT + tolerance


def test_inverted_factor_detection():
    engine = ICWeightEngine()
    for i in range(80):
        fwd = 0.01 * (i % 10 - 5)
        engine.update({
            "normal": fwd * 10,
            "inverted": -fwd * 10,  # negatively correlated
            "neutral": 0.5,
        }, fwd)
    engine.recompute_weights()
    # The inverted factor should be detected
    inv_state = engine.get_factor_state("inverted")
    if inv_state and inv_state.n_obs >= MIN_OBSERVATIONS:
        assert inv_state.ic < 0


def test_get_all_states():
    engine = ICWeightEngine()
    for i in range(60):
        engine.update({"a": i * 0.1, "b": -i * 0.1}, i * 0.01)
    engine.recompute_weights()
    states = engine.get_all_states()
    assert "a" in states
    assert "b" in states
    assert "ic" in states["a"]
    assert "weight" in states["a"]


def test_stuck_factor_pathology_zeroed():
    """Slowly-updating factors (news/fear_greed/macro) can produce only a few
    unique (score, fwd_return) pairs across N obs. Spearman returns ±1.0 in
    that case from the few non-tied points, but it's not a real signal —
    it's the scheduler feeding the same hourly bar's data N times.

    The unique-floor guard zeros IC for factors with insufficient distinct
    score or forward-return values (≥10% of n_obs, with absolute floor of 5).
    """
    engine = ICWeightEngine()
    # 200 observations: 197 identical pairs + 3 distinct pairs that happen
    # to align monotonically (the exact pattern observed in production).
    pairs = [(-0.0016, -0.0231)] * 197 + [
        (0.0018, -0.0105),
        (0.0076, -0.0089),
        (0.0086, -0.0026),
    ]
    for sc, fwd in pairs:
        engine.update({"news_sentiment_stuck": sc}, fwd)
    weights = engine.recompute_weights()
    state = engine.get_factor_state("news_sentiment_stuck")
    assert state is not None
    assert state.n_obs == 200
    assert state.ic == 0.0, f"stuck factor must report ic=0, got {state.ic}"
    assert state.weight == 0.0
    # Should not appear with positive weight in the recomputed weights
    assert weights.get("news_sentiment_stuck", 0.0) == 0.0


def test_diverse_factor_unaffected_by_unique_floor():
    """Factors with normal variability (≥10% unique values) compute IC normally."""
    engine = ICWeightEngine()
    for i in range(80):
        # Both score and fwd vary with n — 80 unique values each
        score = math.sin(i / 7) * 0.5
        fwd = math.sin(i / 7) * 0.01 + 0.0001 * (i % 3)
        engine.update({"diverse_factor": score}, fwd)
    engine.recompute_weights()
    state = engine.get_factor_state("diverse_factor")
    assert state is not None
    assert state.n_obs == 80
    assert abs(state.ic) > 0.5, f"diverse factor should have nonzero IC, got {state.ic}"
