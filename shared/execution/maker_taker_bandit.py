"""Contextual Multi-Armed Bandit for maker/taker order-type decisions.

Why this exists
---------------
Every order has a hidden choice: place a limit order at the best bid/ask
(maker — pay a maker fee, often negative, but risk no fill or adverse
selection) or hit the book with a market order (taker — pay the taker
fee, guaranteed fill but worse fill price). The "right" choice depends
on regime — wide spread + low urgency → maker; tight spread + high
urgency → taker.

Hard-coded rules age badly. A bandit can learn the right policy from
realized slippage feedback.

Design
------
**Discretized contextual bandit** — same Normal-Inverse-Gamma posterior
as FormulaMAB (services/crypto-agent/app/core/bandit.py), but the
"context" is a 4-tuple bucket key (spread × vol × size × urgency). Each
bucket has two arms (MAKER, TAKER) with independent posteriors.

Reward signal
-------------
After fill, the V2 TCA helper computes:
    realized_slippage_bp = (signed) bps adverse to trader
The bandit reward is -realized_slippage_bp / 100, so MAKER strategies
that paid 5bp adverse slippage get reward = -0.05; a TAKER strategy with
0bp slippage gets reward = 0. Higher reward = better choice.

Exploration
-----------
Thompson sampling does the heavy lifting (wide posteriors → exploration
when uncertain). An optional epsilon-greedy floor (default 0.15) forces
the loser arm to be tried even when the winner has a strong lead — so
slow concept drift doesn't lock the bandit into a stale answer.

Persistence
-----------
Arm posteriors serialize the same way as FormulaMAB (n, mean, m2,
total_reward). Use shared.learning.persist-style JSON round-trip for
Redis storage.

Pure Python — no NumPy required for the core. Tests run without I/O.
"""
from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass, field
from typing import Literal

OrderType = Literal["MAKER", "TAKER"]


# ──────────────────────────────────────────────────────────────────
# Context bucketing
# ──────────────────────────────────────────────────────────────────


_SPREAD_EDGES_BP = (5.0, 20.0)
_VOL_EDGES_ANNUAL = (0.15, 0.50)
_SIZE_EDGES_USD = (1_000.0, 10_000.0)
_URGENCY_LEVELS = ("low", "normal", "high")


def _bucket(value: float, edges: tuple[float, ...]) -> int:
    """Return 0..len(edges) bucket index — left-inclusive bins."""
    for i, edge in enumerate(edges):
        if value < edge:
            return i
    return len(edges)


def context_key(
    *,
    spread_bp: float,
    annualized_vol: float,
    order_size_usd: float,
    urgency: str,
) -> str:
    """Discretized bucket key — used as the row dimension of the MAB table.

    Format: 's{0-2}_v{0-2}_z{0-2}_u{low|normal|high}' so eyeballing the
    Redis dump (or Grafana table) is doable.
    """
    s = _bucket(max(spread_bp, 0.0), _SPREAD_EDGES_BP)
    v = _bucket(max(annualized_vol, 0.0), _VOL_EDGES_ANNUAL)
    z = _bucket(max(order_size_usd, 0.0), _SIZE_EDGES_USD)
    u = urgency.lower() if urgency.lower() in _URGENCY_LEVELS else "normal"
    return f"s{s}_v{v}_z{z}_u{u}"


# ──────────────────────────────────────────────────────────────────
# Per-arm sufficient stats (mirrors crypto-agent FormulaMAB)
# ──────────────────────────────────────────────────────────────────


@dataclass
class _Arm:
    n: int = 0
    mean: float = 0.0
    m2: float = 0.0
    total_reward: float = 0.0

    @property
    def variance(self) -> float:
        if self.n < 2:
            return 1.0  # wide prior when uncertain
        return self.m2 / (self.n - 1)

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    def update(self, reward: float, gamma: float = 0.95) -> None:
        self.n += 1
        self.total_reward += reward
        if self.n == 1:
            self.mean = reward
            self.m2 = 0.0
        else:
            self.mean = gamma * self.mean + (1 - gamma) * reward
            delta = reward - self.mean
            self.m2 = gamma * self.m2 + (1 - gamma) * delta * delta

    def sample(self) -> float:
        if self.n == 0:
            return random.gauss(0, 0.5)
        posterior_std = self.std / math.sqrt(self.n)
        exploration_bonus = 0.05 / math.sqrt(self.n)
        return random.gauss(self.mean, posterior_std + exploration_bonus)


# ──────────────────────────────────────────────────────────────────
# MakerTakerBandit
# ──────────────────────────────────────────────────────────────────


try:
    from shared.observability_v3 import (
        MAKER_TAKER_ARM_MEAN_REWARD,
        record_maker_taker_decision,
    )
    _METRICS_AVAILABLE = True
except Exception:
    MAKER_TAKER_ARM_MEAN_REWARD = None  # type: ignore
    record_maker_taker_decision = None  # type: ignore
    _METRICS_AVAILABLE = False


@dataclass
class MakerTakerBandit:
    """Per-(context, action) Thompson-sampling bandit.

    Use:
        bandit = MakerTakerBandit(epsilon=0.15)

        # Decide:
        ctx = context_key(spread_bp=15, annualized_vol=0.30,
                          order_size_usd=2500, urgency="normal")
        action = bandit.select(ctx)   # 'MAKER' or 'TAKER'

        # After fill / cancellation, feed back the realized cost:
        reward = -realized_slippage_bp / 100.0
        bandit.update(ctx, action, reward)
    """

    epsilon: float = 0.15

    # ctx → {"MAKER": _Arm, "TAKER": _Arm}
    _arms: dict[str, dict[OrderType, _Arm]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ─── selection ───────────────────────────────────────────────

    def select(self, ctx: str) -> OrderType:
        """Choose MAKER or TAKER for the given context bucket."""
        # Epsilon-greedy floor: with probability ε, force exploration.
        if random.random() < self.epsilon:
            choice = random.choice(("MAKER", "TAKER"))
        else:
            with self._lock:
                arms = self._ensure_arms(ctx)
                samples = {a: arms[a].sample() for a in ("MAKER", "TAKER")}
            choice = max(samples, key=lambda a: samples[a])  # type: ignore[assignment]
        if _METRICS_AVAILABLE and record_maker_taker_decision is not None:
            try:
                record_maker_taker_decision(ctx, choice, realized_slippage_bp=None)
            except Exception:
                pass
        return choice  # type: ignore[return-value]

    def update(self, ctx: str, action: OrderType, reward: float) -> None:
        """Push realized reward back to the chosen arm.

        `reward` should be the bandit-scale reward (typically
        -realized_slippage_bp / 100). If you want the slippage histogram
        populated too, call `record_maker_taker_decision(ctx, action,
        realized_slippage_bp=...)` from the execution layer at fill time.
        """
        if action not in ("MAKER", "TAKER"):
            raise ValueError(f"action must be MAKER or TAKER, got {action!r}")
        with self._lock:
            arms = self._ensure_arms(ctx)
            arms[action].update(reward)
            mean = arms[action].mean
        if _METRICS_AVAILABLE and MAKER_TAKER_ARM_MEAN_REWARD is not None:
            try:
                MAKER_TAKER_ARM_MEAN_REWARD.labels(context=ctx, action=action).set(float(mean))
            except Exception:
                pass

    # ─── inspection ──────────────────────────────────────────────

    def get_stats(self) -> dict[str, dict[OrderType, dict]]:
        """Per-ctx, per-arm stats — for dashboards / Redis serialization."""
        out: dict[str, dict[OrderType, dict]] = {}
        with self._lock:
            for ctx, arms in self._arms.items():
                out[ctx] = {
                    a: {
                        "n": arm.n,
                        "mean_reward": round(arm.mean, 6),
                        "std": round(arm.std, 6),
                        "total_reward": round(arm.total_reward, 6),
                    }
                    for a, arm in arms.items()
                }
        return out

    def best_arm(self, ctx: str) -> OrderType:
        """The arm with the higher posterior mean (no sampling)."""
        with self._lock:
            arms = self._ensure_arms(ctx)
            return "MAKER" if arms["MAKER"].mean >= arms["TAKER"].mean else "TAKER"

    def n_observations(self, ctx: str) -> int:
        with self._lock:
            if ctx not in self._arms:
                return 0
            return sum(a.n for a in self._arms[ctx].values())

    # ─── persistence ─────────────────────────────────────────────

    def serialize(self) -> dict:
        """JSON-safe dict snapshot."""
        return {
            "epsilon": self.epsilon,
            "arms": {
                ctx: {
                    a: {
                        "n": arm.n,
                        "mean": arm.mean,
                        "m2": arm.m2,
                        "total_reward": arm.total_reward,
                    }
                    for a, arm in arms.items()
                }
                for ctx, arms in self._arms.items()
            },
        }

    @classmethod
    def deserialize(cls, data: dict) -> "MakerTakerBandit":
        bandit = cls(epsilon=float(data.get("epsilon", 0.15)))
        for ctx, arms_data in data.get("arms", {}).items():
            arms: dict[OrderType, _Arm] = {}
            for a, ad in arms_data.items():
                arm = _Arm()
                arm.n = int(ad.get("n", 0))
                arm.mean = float(ad.get("mean", 0.0))
                arm.m2 = float(ad.get("m2", 0.0))
                arm.total_reward = float(ad.get("total_reward", 0.0))
                arms[a] = arm  # type: ignore[index]
            bandit._arms[ctx] = arms
        return bandit

    # ─── internal ────────────────────────────────────────────────

    def _ensure_arms(self, ctx: str) -> dict[OrderType, _Arm]:
        if ctx not in self._arms:
            self._arms[ctx] = {"MAKER": _Arm(), "TAKER": _Arm()}
        return self._arms[ctx]


# ──────────────────────────────────────────────────────────────────
# Reward translation (V2 TCA helper integration)
# ──────────────────────────────────────────────────────────────────


def slippage_to_reward(realized_slippage_bp: float) -> float:
    """Map adverse-oriented slippage (bps) to bandit reward.

    The V2 TCA helper (services/crypto-agent/app/core/tca.py) returns
    `realized_slippage_bp` oriented so positive = adverse. The bandit
    optimizes for max reward, so we invert and scale to roughly [-1, 1].
    1bp ≈ 0.01 reward unit so a typical 5-20bp range maps to [-0.2, -0.05].
    """
    return -float(realized_slippage_bp) / 100.0
