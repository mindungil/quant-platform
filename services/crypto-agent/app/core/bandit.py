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

import math
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc

from shared.logging import get_logger

logger = get_logger("crypto-agent")


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
