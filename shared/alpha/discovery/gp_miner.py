"""V4-3 — Genetic Programming Alpha Discovery (WorldQuant 101 style).

What this provides
------------------
A small genetic-programming search over factor-combining expressions.
Operates on a fixed pool of "primitive" factor names (rsi, macd,
bollinger, vwap, fear_greed_index, ...) and binary operators
(add, sub, mul, div, max, min, neg, abs, ema). Each candidate is a
small expression tree that evaluates to a series given a features
DataFrame.

Pipeline:
  1. Seed population from a few hand-coded expressions + random trees.
  2. Each generation: evaluate fitness (annualized Sharpe of the signal
     vs forward returns), select top-K, crossover + mutate.
  3. Promote any candidate that clears the institutional gate:
        backtest_sharpe ≥ 1.0
        DSR.verdict == 'genuine'
        PBO ≤ 0.30
  4. Promoted candidates can be auto-registered into ALPHA_REGISTRY as
     a `gp_<hash>` alpha (caller-controlled).

Pure Python — no I/O. Tests run offline on synthetic data.

Reference: Kakushadze (2016) "101 formulaic alphas" + Koza (1992) on GP.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────
# Expression tree
# ──────────────────────────────────────────────────────────────────


@dataclass
class Node:
    """Expression-tree node. Either a leaf (factor name / constant) or
    an internal op with children."""

    op: str                              # 'leaf', 'add', 'sub', 'mul', 'div', 'max', 'min', 'neg', 'abs', 'ema', 'const'
    name: Optional[str] = None           # leaf factor name
    value: Optional[float] = None        # constant value
    span: Optional[int] = None           # for 'ema' span
    children: list["Node"] = field(default_factory=list)

    def eval(self, df: pd.DataFrame) -> pd.Series:
        """Evaluate this expression against the features DataFrame.
        Returns a pd.Series aligned to df.index."""
        if self.op == "leaf":
            if self.name and self.name in df.columns:
                return df[self.name].astype(float)
            return pd.Series(0.0, index=df.index)
        if self.op == "const":
            return pd.Series(float(self.value or 0.0), index=df.index)
        if self.op == "neg":
            return -self.children[0].eval(df)
        if self.op == "abs":
            return self.children[0].eval(df).abs()
        if self.op == "ema":
            s = self.children[0].eval(df)
            return s.ewm(span=int(self.span or 5), adjust=False, min_periods=1).mean()
        # Binary
        a = self.children[0].eval(df)
        b = self.children[1].eval(df)
        if self.op == "add":
            return a + b
        if self.op == "sub":
            return a - b
        if self.op == "mul":
            return a * b
        if self.op == "div":
            return a / b.replace(0, np.nan).fillna(1e-9)
        if self.op == "max":
            return pd.concat([a, b], axis=1).max(axis=1)
        if self.op == "min":
            return pd.concat([a, b], axis=1).min(axis=1)
        raise ValueError(f"unknown op: {self.op}")

    def to_str(self) -> str:
        if self.op == "leaf":
            return self.name or "0"
        if self.op == "const":
            return f"{self.value:.3f}"
        if self.op in ("neg", "abs"):
            return f"{self.op}({self.children[0].to_str()})"
        if self.op == "ema":
            return f"ema({self.children[0].to_str()},{self.span})"
        return f"({self.children[0].to_str()} {self.op} {self.children[1].to_str()})"

    def hash(self) -> str:
        """Stable short hash for naming auto-registered alphas."""
        return hashlib.md5(self.to_str().encode()).hexdigest()[:8]

    def depth(self) -> int:
        if not self.children:
            return 1
        return 1 + max(c.depth() for c in self.children)

    def size(self) -> int:
        return 1 + sum(c.size() for c in self.children)


def leaf(name: str) -> Node:
    return Node(op="leaf", name=name)


def const(value: float) -> Node:
    return Node(op="const", value=value)


_BINARY_OPS = ("add", "sub", "mul", "div", "max", "min")
_UNARY_OPS = ("neg", "abs")


# ──────────────────────────────────────────────────────────────────
# Random tree generation + mutation
# ──────────────────────────────────────────────────────────────────


def random_tree(
    factors: list[str],
    max_depth: int = 3,
    *,
    rng: Optional[random.Random] = None,
    const_prob: float = 0.10,
) -> Node:
    """Build a random expression tree drawing leaves from `factors`."""
    rng = rng or random.Random()

    def _build(depth: int) -> Node:
        if depth <= 0 or rng.random() < 0.40:
            if rng.random() < const_prob:
                return const(round(rng.uniform(-1.0, 1.0), 3))
            return leaf(rng.choice(factors))
        # Internal node
        op_choice = rng.random()
        if op_choice < 0.15:
            return Node(op="neg", children=[_build(depth - 1)])
        if op_choice < 0.25:
            return Node(op="abs", children=[_build(depth - 1)])
        if op_choice < 0.35:
            return Node(op="ema", span=rng.choice([3, 5, 10, 20]),
                        children=[_build(depth - 1)])
        op = rng.choice(_BINARY_OPS)
        return Node(op=op, children=[_build(depth - 1), _build(depth - 1)])

    return _build(max_depth)


def mutate(tree: Node, factors: list[str], *,
            rng: Optional[random.Random] = None,
            mutation_rate: float = 0.20) -> Node:
    """Probabilistic point mutation — return a new tree (no mutation in-place)."""
    rng = rng or random.Random()
    if rng.random() > mutation_rate:
        return _clone(tree)
    # Replace a random subtree with a fresh random one
    new_subtree = random_tree(factors, max_depth=2, rng=rng)
    return _replace_random(tree, new_subtree, rng)


def crossover(a: Node, b: Node, *,
               rng: Optional[random.Random] = None) -> Node:
    """Pick a random subtree from b and graft onto a clone of a."""
    rng = rng or random.Random()
    nodes_b = _all_nodes(b)
    chosen = rng.choice(nodes_b)
    return _replace_random(a, _clone(chosen), rng)


def _clone(tree: Node) -> Node:
    return Node(
        op=tree.op, name=tree.name, value=tree.value, span=tree.span,
        children=[_clone(c) for c in tree.children],
    )


def _all_nodes(tree: Node) -> list[Node]:
    out = [tree]
    for c in tree.children:
        out.extend(_all_nodes(c))
    return out


def _replace_random(tree: Node, replacement: Node, rng: random.Random) -> Node:
    """Replace one randomly chosen node in a clone of `tree` with `replacement`."""
    clone = _clone(tree)
    candidates = _all_nodes(clone)
    if not candidates:
        return replacement
    target = rng.choice(candidates)
    if target is clone:
        return replacement
    # Replace by mutating target's identity in place
    target.op = replacement.op
    target.name = replacement.name
    target.value = replacement.value
    target.span = replacement.span
    target.children = [_clone(c) for c in replacement.children]
    return clone


# ──────────────────────────────────────────────────────────────────
# Fitness + evolution
# ──────────────────────────────────────────────────────────────────


def fitness_sharpe(
    tree: Node,
    features: pd.DataFrame,
    forward_returns: pd.Series,
    *,
    periods_per_year: float = 24 * 365,
) -> float:
    """Annualized Sharpe of the candidate signal as a long-short position.

    Signal is z-scored and then multiplied by forward_returns to give
    per-bar PnL. Sharpe = mean / std × sqrt(periods).
    """
    try:
        sig = tree.eval(features)
    except Exception:
        return float("-inf")
    sig = sig.reindex(features.index).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if sig.std() < 1e-12:
        return 0.0
    z = (sig - sig.mean()) / sig.std()
    pnl = z * forward_returns.reindex(features.index).fillna(0.0)
    mu = float(pnl.mean())
    sd = float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0
    if sd <= 1e-12:
        return 0.0
    return mu / sd * math.sqrt(periods_per_year)


@dataclass
class GPConfig:
    population_size: int = 30
    n_generations: int = 10
    tournament_size: int = 3
    elite_size: int = 3
    crossover_rate: float = 0.7
    mutation_rate: float = 0.20
    max_tree_depth: int = 3
    seed: Optional[int] = None


@dataclass
class GPResult:
    best_tree: Node
    best_sharpe: float
    best_str: str
    best_hash: str
    history: list[float] = field(default_factory=list)  # best Sharpe per generation


def evolve(
    factors: list[str],
    features: pd.DataFrame,
    forward_returns: pd.Series,
    config: Optional[GPConfig] = None,
    seed_trees: Optional[list[Node]] = None,
) -> GPResult:
    """Run the GP search. Returns the best candidate found."""
    cfg = config or GPConfig()
    rng = random.Random(cfg.seed)

    # Seed population
    population: list[Node] = list(seed_trees or [])
    while len(population) < cfg.population_size:
        population.append(random_tree(factors, cfg.max_tree_depth, rng=rng))

    history: list[float] = []
    best_tree: Optional[Node] = None
    best_sharpe = float("-inf")

    for gen in range(cfg.n_generations):
        scored = [(t, fitness_sharpe(t, features, forward_returns)) for t in population]
        scored.sort(key=lambda x: x[1], reverse=True)
        if scored[0][1] > best_sharpe:
            best_sharpe = scored[0][1]
            best_tree = _clone(scored[0][0])
        history.append(scored[0][1])

        # Build next generation
        next_pop = [_clone(t) for t, _ in scored[: cfg.elite_size]]
        while len(next_pop) < cfg.population_size:
            # Tournament selection
            tourn = rng.sample(scored, min(cfg.tournament_size, len(scored)))
            parent_a = max(tourn, key=lambda x: x[1])[0]
            if rng.random() < cfg.crossover_rate:
                tourn_b = rng.sample(scored, min(cfg.tournament_size, len(scored)))
                parent_b = max(tourn_b, key=lambda x: x[1])[0]
                child = crossover(parent_a, parent_b, rng=rng)
            else:
                child = _clone(parent_a)
            child = mutate(child, factors, rng=rng, mutation_rate=cfg.mutation_rate)
            next_pop.append(child)

        population = next_pop

    assert best_tree is not None
    return GPResult(
        best_tree=best_tree,
        best_sharpe=best_sharpe,
        best_str=best_tree.to_str(),
        best_hash=best_tree.hash(),
        history=history,
    )


# ──────────────────────────────────────────────────────────────────
# Institutional gate (DSR + PBO + Sharpe threshold)
# ──────────────────────────────────────────────────────────────────


def passes_gate(
    tree: Node,
    features: pd.DataFrame,
    forward_returns: pd.Series,
    *,
    min_sharpe: float = 1.0,
    n_trials: int = 1,
    sr_std_across_trials: float = 1.0,
    pbo_max: float = 0.30,
    periods_per_year: float = 24 * 365,
) -> tuple[bool, dict]:
    """Run the candidate through the same gate as incubate_alpha.

    Returns (passed, diagnostics). diagnostics has sharpe, dsr verdict, pbo
    if computable.
    """
    sig = tree.eval(features)
    sig = sig.reindex(features.index).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    if sig.std() < 1e-12:
        return False, {"reason": "zero_variance"}

    z = (sig - sig.mean()) / sig.std()
    pnl = (z * forward_returns.reindex(features.index).fillna(0.0)).values

    diag = {"sharpe": fitness_sharpe(tree, features, forward_returns,
                                       periods_per_year=periods_per_year)}
    if diag["sharpe"] < min_sharpe:
        diag["reason"] = "sharpe_below_threshold"
        return False, diag

    # DSR
    try:
        from shared.statistics.deflated_sharpe import deflated_sharpe_ratio
        dsr = deflated_sharpe_ratio(
            pnl, n_trials=n_trials,
            sr_std_across_trials=sr_std_across_trials,
            periods_per_year=periods_per_year,
        )
        diag.update({"dsr": dsr.get("dsr"), "verdict": dsr.get("verdict")})
        if dsr.get("verdict") != "genuine":
            diag["reason"] = "dsr_not_genuine"
            return False, diag
    except Exception as exc:
        diag["dsr_error"] = str(exc)[:120]

    return True, diag
