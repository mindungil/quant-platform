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
