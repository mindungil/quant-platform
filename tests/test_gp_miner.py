"""Tests for shared.alpha.discovery.gp_miner — V4-3."""
from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from shared.alpha.discovery.gp_miner import (
    GPConfig,
    Node,
    const,
    crossover,
    evolve,
    fitness_sharpe,
    leaf,
    mutate,
    passes_gate,
    random_tree,
)


# ─── Node basics ──────────────────────────────────────────────────


def _make_features(n: int = 500, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({
        "rsi": rng.uniform(-1, 1, n),
        "macd": rng.normal(0, 0.5, n),
        "bollinger": rng.uniform(-1, 1, n),
        "vwap": rng.normal(0, 0.3, n),
        "fear_greed_index": rng.uniform(-1, 1, n),
    }, index=idx)


def test_leaf_eval_returns_column() -> None:
    df = _make_features(50)
    t = leaf("rsi")
    out = t.eval(df)
    pd.testing.assert_series_equal(out, df["rsi"].astype(float), check_names=False)


def test_leaf_unknown_factor_returns_zero() -> None:
    df = _make_features(20)
    out = leaf("never_seen").eval(df)
    assert (out == 0.0).all()


def test_const_eval() -> None:
    df = _make_features(30)
    out = const(0.5).eval(df)
    assert (out == 0.5).all()


def test_binary_op_eval() -> None:
    df = _make_features(40)
    t = Node(op="add", children=[leaf("rsi"), leaf("macd")])
    out = t.eval(df)
    pd.testing.assert_series_equal(out, (df["rsi"] + df["macd"]).astype(float),
                                    check_names=False)


def test_div_handles_zero_denominator() -> None:
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [1.0, 0.0, 2.0]})
    t = Node(op="div", children=[leaf("a"), leaf("b")])
    out = t.eval(df)
    assert all(np.isfinite(out))


def test_unary_neg_and_abs() -> None:
    df = pd.DataFrame({"x": [1.0, -2.0, 3.0]})
    n = Node(op="neg", children=[leaf("x")])
    a = Node(op="abs", children=[leaf("x")])
    assert list(n.eval(df)) == [-1.0, 2.0, -3.0]
    assert list(a.eval(df)) == [1.0, 2.0, 3.0]


def test_ema_eval_smooths() -> None:
    df = pd.DataFrame({"x": [0.0, 1.0, 0.0, 1.0, 0.0]})
    t = Node(op="ema", span=3, children=[leaf("x")])
    out = t.eval(df)
    # EMA of alternating should be in (0, 1)
    assert 0 < out.iloc[-1] < 1


def test_to_str_round_trippable_via_hash() -> None:
    t = Node(op="add", children=[leaf("rsi"), const(0.5)])
    s = t.to_str()
    assert "rsi" in s and "0.500" in s
    assert len(t.hash()) == 8


def test_depth_and_size() -> None:
    t = Node(op="add", children=[
        Node(op="mul", children=[leaf("a"), leaf("b")]),
        leaf("c"),
    ])
    assert t.depth() == 3
    assert t.size() == 5


# ─── Random tree / mutation / crossover ───────────────────────────


def test_random_tree_within_depth_limit() -> None:
    rng = random.Random(7)
    factors = ["a", "b", "c"]
    for _ in range(20):
        t = random_tree(factors, max_depth=3, rng=rng)
        assert t.depth() <= 4


def test_mutate_returns_new_tree() -> None:
    rng = random.Random(7)
    t = Node(op="add", children=[leaf("a"), leaf("b")])
    new = mutate(t, ["a", "b", "c"], rng=rng, mutation_rate=1.0)
    # mutation_rate=1.0 → always mutates; structure may differ
    assert isinstance(new, Node)


def test_mutate_no_change_when_rate_zero() -> None:
    rng = random.Random(7)
    t = Node(op="add", children=[leaf("a"), leaf("b")])
    new = mutate(t, ["a", "b"], rng=rng, mutation_rate=0.0)
    assert new.to_str() == t.to_str()


def test_crossover_combines() -> None:
    rng = random.Random(7)
    a = Node(op="add", children=[leaf("rsi"), leaf("macd")])
    b = Node(op="mul", children=[leaf("vwap"), leaf("bollinger")])
    out = crossover(a, b, rng=rng)
    # out is a clone-and-graft, shape mixed
    s = out.to_str()
    assert any(name in s for name in ("rsi", "macd", "vwap", "bollinger"))


# ─── Fitness ──────────────────────────────────────────────────────


def test_fitness_zero_signal_returns_zero() -> None:
    df = _make_features(100)
    fr = pd.Series(0.001, index=df.index)
    t = const(0.0)
    assert fitness_sharpe(t, df, fr) == 0.0


def test_fitness_constant_signal_returns_zero() -> None:
    df = _make_features(100)
    fr = pd.Series(0.001, index=df.index)
    t = leaf("rsi")
    df["rsi"] = 0.5
    assert fitness_sharpe(t, df, fr) == 0.0


def test_fitness_picks_up_aligned_signal() -> None:
    """Signal that's literally the forward return should yield very high Sharpe."""
    rng = np.random.default_rng(0)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    fr = pd.Series(rng.normal(0, 0.01, n), index=idx)
    df = pd.DataFrame({"perfect": fr.values}, index=idx)
    t = leaf("perfect")
    # signal_aligned_to_returns × returns → huge Sharpe
    sh = fitness_sharpe(t, df, fr)
    assert sh > 50  # essentially perfect prediction


# ─── evolve ───────────────────────────────────────────────────────


def test_evolve_returns_valid_result() -> None:
    df = _make_features(300, seed=1)
    fr = df["rsi"] * 0.005  # rsi is the predictive factor
    cfg = GPConfig(population_size=10, n_generations=5,
                    max_tree_depth=2, seed=42)
    result = evolve(
        factors=["rsi", "macd", "bollinger", "vwap"],
        features=df,
        forward_returns=fr,
        config=cfg,
    )
    assert result.best_tree is not None
    assert len(result.history) == 5
    # Best Sharpe should be > 0 (we planted a real signal)
    assert result.best_sharpe > 0


def test_evolve_history_non_decreasing_at_best() -> None:
    df = _make_features(500, seed=3)
    fr = pd.Series(np.random.default_rng(3).normal(0, 0.01, 500), index=df.index)
    cfg = GPConfig(population_size=20, n_generations=6, seed=7)
    result = evolve(
        factors=["rsi", "macd", "bollinger", "vwap", "fear_greed_index"],
        features=df, forward_returns=fr, config=cfg,
    )
    # Best across all generations is the final reported best
    assert result.best_sharpe >= max(result.history)


def test_evolve_seed_trees_included() -> None:
    df = _make_features(200)
    fr = pd.Series(0.001, index=df.index)
    seed = [leaf("rsi"), Node(op="add", children=[leaf("rsi"), leaf("macd")])]
    cfg = GPConfig(population_size=4, n_generations=2, seed=1)
    result = evolve(
        factors=["rsi", "macd"], features=df, forward_returns=fr,
        config=cfg, seed_trees=seed,
    )
    # No assertion on which seed wins — just no crash and best_tree set
    assert result.best_tree is not None


# ─── Gate ────────────────────────────────────────────────────────


def test_gate_rejects_zero_variance() -> None:
    df = _make_features(200)
    fr = pd.Series(0.001, index=df.index)
    ok, diag = passes_gate(const(0.5), df, fr)
    assert not ok
    assert diag["reason"] == "zero_variance"


def test_gate_rejects_below_sharpe_threshold() -> None:
    df = _make_features(500, seed=5)
    fr = pd.Series(np.random.default_rng(5).normal(0, 0.01, 500), index=df.index)
    # Random factor → ~0 Sharpe
    ok, diag = passes_gate(leaf("rsi"), df, fr, min_sharpe=5.0)
    assert not ok
    assert "sharpe" in diag


def test_gate_passes_strong_signal() -> None:
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    rng = np.random.default_rng(0)
    fr = pd.Series(rng.normal(0, 0.01, n), index=idx)
    df = pd.DataFrame({"perfect": fr.values}, index=idx)
    ok, diag = passes_gate(leaf("perfect"), df, fr, min_sharpe=2.0)
    assert ok
    assert diag.get("verdict") == "genuine"
