"""Thompson Sampling Multi-Armed Bandit for formula selection.

Each formula is an "arm". We model each arm's reward distribution as a
Normal distribution with unknown mean and variance (Normal-Inverse-Gamma conjugate prior).

For each decision:
1. Sample from each arm's posterior distribution
2. Pick the arm with highest sampled value
3. After observing the trade outcome, update that arm's posterior

This naturally balances exploration vs exploitation:
- Arms with few observations have wide posteriors → more likely to be sampled high
- Arms with many observations have narrow posteriors → converge to true mean
- As data accumulates, the algorithm converges to the optimal arm
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc

from shared.logging import get_logger

logger = get_logger("crypto-agent")

# Redis persistence keys
_REDIS_KEY_GLOBAL = "mab:formula:global"
_REDIS_KEY_REGIMES = "mab:formula:regimes"
_REDIS_SAVE_EVERY_N = 10  # save after every N updates to avoid hot writes


def _get_redis_client():
    """Lazy redis import — never raise on missing module/connection."""
    try:
        import redis
        return redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
            socket_timeout=2,
        )
    except Exception:
        return None


@dataclass
class ArmState:
    """Sufficient statistics for Normal-Inverse-Gamma posterior."""
    name: str
    n: int = 0              # number of observations
    mean: float = 0.0       # running mean of rewards
    m2: float = 0.0         # running sum of squared deviations (for variance)
    total_reward: float = 0.0
    last_updated: datetime | None = None

    @property
    def variance(self) -> float:
        if self.n < 2:
            return 1.0  # high prior variance when little data
        return self.m2 / (self.n - 1)

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    def update(self, reward: float, gamma: float = 0.95) -> None:
        """Online update using exponential discounting for regime adaptivity.

        Recent observations are weighted more heavily than older ones,
        allowing the bandit to adapt when formula performance shifts.
        Falls back to simple initialization on first observation.
        """
        self.n += 1
        self.total_reward += reward
        if self.n == 1:
            self.mean = reward
            self.m2 = 0.0
        else:
            self.mean = gamma * self.mean + (1 - gamma) * reward
            delta = reward - self.mean
            self.m2 = gamma * self.m2 + (1 - gamma) * delta * delta
        self.last_updated = datetime.now(UTC)

    def sample(self) -> float:
        """Thompson sample: draw from posterior predictive distribution.

        With n observations, posterior is:
          mean ~ Normal(sample_mean, sample_var / n)
        So we sample: mean + Normal(0, 1) * std / sqrt(n)

        With few observations, std/sqrt(n) is large → more exploration.
        With many observations, std/sqrt(n) is small → more exploitation.
        """
        if self.n == 0:
            # No data: sample from wide prior N(0, 1)
            return random.gauss(0, 0.05)

        # Posterior standard error of the mean
        posterior_std = self.std / math.sqrt(self.n)

        # Add a minimum exploration bonus that decays with sqrt(n)
        exploration_bonus = 0.01 / math.sqrt(self.n)

        sampled = random.gauss(self.mean, posterior_std + exploration_bonus)
        return sampled


class FormulaMAB:
    """Multi-Armed Bandit for formula selection using Thompson Sampling.

    Usage:
        mab = FormulaMAB(["momentum_ema_cross", "mean_reversion_bb", ...])

        # Select: Thompson sample from each arm, pick highest
        selected = mab.select(regime="trending")

        # Update: after trade completes, report outcome
        mab.update("momentum_ema_cross", reward=0.03)
    """

    def __init__(self, formula_names: list[str]) -> None:
        self._lock = threading.Lock()
        self._arms: dict[str, ArmState] = {
            name: ArmState(name=name) for name in formula_names
        }
        # Per-regime arm states for contextual bandits
        self._regime_arms: dict[str, dict[str, ArmState]] = {}
        # Persistence: try to load from Redis at construction time
        self._update_counter = 0
        try:
            self._load_from_redis()
        except Exception as exc:
            logger.warning("mab_redis_load_failed", extra={"error": str(exc)[:120]})

    def _serialize_arm(self, arm: "ArmState") -> dict:
        return {
            "n": arm.n,
            "mean": arm.mean,
            "m2": arm.m2,
            "total_reward": arm.total_reward,
            "last_updated": arm.last_updated.isoformat() if arm.last_updated else None,
        }

    def _deserialize_arm(self, name: str, data: dict) -> "ArmState":
        a = ArmState(name=name)
        a.n = int(data.get("n", 0))
        a.mean = float(data.get("mean", 0.0))
        a.m2 = float(data.get("m2", 0.0))
        a.total_reward = float(data.get("total_reward", 0.0))
        last = data.get("last_updated")
        if last:
            try:
                a.last_updated = datetime.fromisoformat(last)
            except Exception:
                pass
        return a

    def _save_to_redis(self) -> None:
        """Persist current arm state to Redis (atomic with pipeline)."""
        r = _get_redis_client()
        if r is None:
            return
        try:
            global_payload = {n: self._serialize_arm(a) for n, a in self._arms.items()}
            regimes_payload = {
                regime: {n: self._serialize_arm(a) for n, a in arms.items()}
                for regime, arms in self._regime_arms.items()
            }
            pipe = r.pipeline()
            pipe.set(_REDIS_KEY_GLOBAL, json.dumps(global_payload))
            pipe.set(_REDIS_KEY_REGIMES, json.dumps(regimes_payload))
            pipe.execute()
        except Exception as exc:
            logger.warning("mab_redis_save_failed", extra={"error": str(exc)[:120]})

    def _load_from_redis(self) -> int:
        """Restore arm state from Redis. Returns total observations loaded."""
        r = _get_redis_client()
        if r is None:
            return 0
        loaded = 0
        try:
            raw_global = r.get(_REDIS_KEY_GLOBAL)
            if raw_global:
                payload = json.loads(raw_global)
                for name, data in payload.items():
                    arm = self._deserialize_arm(name, data)
                    self._arms[name] = arm
                    loaded += arm.n
            raw_regimes = r.get(_REDIS_KEY_REGIMES)
            if raw_regimes:
                payload = json.loads(raw_regimes)
                for regime, arms in payload.items():
                    self._regime_arms[regime] = {
                        n: self._deserialize_arm(n, d) for n, d in arms.items()
                    }
        except Exception as exc:
            logger.warning("mab_redis_load_inner_failed", extra={"error": str(exc)[:120]})
        if loaded > 0:
            logger.info("mab_redis_loaded", extra={"observations": loaded})
        return loaded

    def force_save(self) -> None:
        """Public method to force a Redis save (used by hindsight loops)."""
        with self._lock:
            self._save_to_redis()

    def _get_regime_arms(self, regime: str) -> dict[str, ArmState]:
        """Get or create per-regime arm states."""
        if regime not in self._regime_arms:
            self._regime_arms[regime] = {
                name: ArmState(name=name) for name in self._arms
            }
        return self._regime_arms[regime]

    def select(self, regime: str | None = None, eligible: list[str] | None = None) -> str:
        """Select a formula using Thompson Sampling.

        Args:
            regime: Current market regime label (for contextual bandits).
                    If provided, uses regime-specific arm states.
            eligible: Optional list of eligible formula names to choose from.

        Returns:
            Name of the selected formula.
        """
        with self._lock:
            if regime:
                arms = self._get_regime_arms(regime)
            else:
                arms = self._arms

            candidates = arms
            if eligible:
                candidates = {k: v for k, v in arms.items() if k in eligible}
            if not candidates:
                candidates = arms

            # Thompson sample from each arm
            samples = {name: arm.sample() for name, arm in candidates.items()}

            # Pick the arm with highest sampled value
            selected = max(samples, key=samples.get)  # type: ignore[arg-type]

            logger.info(
                "mab_selection",
                extra={
                    "selected": selected,
                    "regime": regime or "global",
                    "samples": {k: round(v, 6) for k, v in sorted(samples.items(), key=lambda x: -x[1])[:5]},
                    "arm_stats": {
                        name: {"n": arm.n, "mean": round(arm.mean, 4)}
                        for name, arm in candidates.items()
                    },
                },
            )

            return selected

    def update(self, formula_name: str, reward: float, regime: str | None = None) -> None:
        """Update arm after observing trade outcome.

        Args:
            formula_name: Which formula was used.
            reward: The trade PnL (positive = good, negative = bad).
            regime: The market regime when the trade was made.
        """
        with self._lock:
            # Update global arm
            if formula_name in self._arms:
                self._arms[formula_name].update(reward)

            # Update regime-specific arm
            if regime:
                regime_arms = self._get_regime_arms(regime)
                if formula_name in regime_arms:
                    regime_arms[formula_name].update(reward)

            # Periodic Redis persistence — every Nth update
            self._update_counter += 1
            if self._update_counter >= _REDIS_SAVE_EVERY_N:
                self._update_counter = 0
                self._save_to_redis()

    def update_from_hindsight(self, formula_name: str, price_change_pct: float, regime: str = "") -> None:
        """Update MAB arm from hindsight analysis (no actual trade needed).

        Reward = price_change_pct normalized to [-1, 1].
        Positive for correct direction, negative for wrong.
        """
        reward = max(-1.0, min(1.0, price_change_pct / 5.0))  # 5% = full reward
        self.update(formula_name, reward, regime=regime or None)

    def load_from_memory(self, memory_items: list[dict]) -> int:
        """Bootstrap arm states from historical memory records.

        Args:
            memory_items: List of memory search results with record.formula_name,
                         record.trade_outcome, record.regime_label.

        Returns:
            Number of observations loaded.
        """
        count = 0
        with self._lock:
            for item in memory_items:
                record = item.get("record", {})
                fname = record.get("formula_name")
                outcome = record.get("trade_outcome")
                regime = record.get("regime_label")

                if fname is None or outcome is None:
                    continue

                # Ensure arm exists
                if fname not in self._arms:
                    self._arms[fname] = ArmState(name=fname)

                self._arms[fname].update(outcome)

                if regime:
                    regime_arms = self._get_regime_arms(regime)
                    if fname not in regime_arms:
                        regime_arms[fname] = ArmState(name=fname)
                    regime_arms[fname].update(outcome)

                count += 1

        logger.info(
            "mab_loaded_from_memory",
            extra={
                "observations_loaded": count,
                "arms": {name: {"n": arm.n, "mean": round(arm.mean, 4)} for name, arm in self._arms.items() if arm.n > 0},
            },
        )
        return count

    def get_stats(self) -> dict:
        """Return current arm statistics for monitoring."""
        with self._lock:
            return {
                "global": {
                    name: {
                        "n": arm.n,
                        "mean": round(arm.mean, 4),
                        "std": round(arm.std, 4),
                        "total_reward": round(arm.total_reward, 4),
                    }
                    for name, arm in self._arms.items()
                },
                "regimes": {
                    regime: {
                        name: {"n": arm.n, "mean": round(arm.mean, 4)}
                        for name, arm in arms.items()
                        if arm.n > 0
                    }
                    for regime, arms in self._regime_arms.items()
                },
            }
