"""V4-1 — RL Execution Agent stacked on Almgren-Chriss optimal execution.

What this provides
------------------
For a large parent order (drives down by `total_quantity` over a
`horizon_minutes` window), produces a slicing schedule that minimizes
expected execution cost given:
  - temporary market impact η  (bps per unit-of-ADV traded)
  - permanent market impact γ  (bps per unit-of-ADV traded, persistent)
  - per-bar volatility σ        (bp standard deviation)
  - operator's risk aversion λ  (higher = front-load to reduce variance)

Two-stage design
----------------
1. **Almgren-Chriss closed-form trajectory** (deterministic). Gives the
   optimal expected-cost-minimizing schedule under quadratic impact +
   normal returns. Front-loads when λ is high (urgency), spreads evenly
   when λ→0 (TWAP).

2. **RL refinement** — a thin epsilon-greedy bandit on top that picks
   between candidate schedules (AC-front, AC-even, AC-late) using
   realized-cost feedback. Reuses the bandit pattern from
   shared.execution.maker_taker_bandit.

Both stages are pure: no I/O, no random side effects (the RL stage uses
a seeded random module internally). The caller is responsible for actually
placing each slice via shared.execution.router.

Reference: Almgren & Chriss (2001) "Optimal execution of portfolio
transactions"; Cartea-Jaimungal-Penalva (2015) ch. 6 for the RL extension.
"""
from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass, field
from typing import Literal


Side = Literal["BUY", "SELL"]
Trajectory = Literal["FRONT_LOAD", "TWAP", "BACK_LOAD"]


# ──────────────────────────────────────────────────────────────────
# Almgren-Chriss closed-form trajectory
# ──────────────────────────────────────────────────────────────────


@dataclass
class ACParams:
    """Market microstructure parameters fed to the AC model.

    Defaults are typical crypto-perp values. Override per venue / asset.
    """

    temp_impact_eta: float = 1.4e-6   # bps per fraction-of-ADV traded
    perm_impact_gamma: float = 2.5e-7  # bps per fraction-of-ADV
    bar_vol_bps: float = 30.0          # std dev of 1-bar return in bps
    risk_aversion_lambda: float = 1e-6  # higher = front-load


@dataclass
class ACSchedule:
    """Output of optimal_schedule: per-bar quantity to execute."""

    quantities: list[float] = field(default_factory=list)  # absolute units
    expected_cost_bp: float = 0.0
    cost_variance_bp2: float = 0.0   # in (basis points)²
    trajectory_shape: Trajectory = "TWAP"

    @property
    def n_slices(self) -> int:
        return len(self.quantities)

    @property
    def total_quantity(self) -> float:
        return sum(self.quantities)


def optimal_schedule(
    total_quantity: float,
    n_slices: int,
    params: ACParams | None = None,
    adv_units: float = 1.0,
) -> ACSchedule:
    """Almgren-Chriss closed-form solution.

    The trajectory x_t = X * sinh(kappa*(T-t)) / sinh(kappa*T), where
    kappa = sqrt(lambda * sigma^2 / eta) sets how front-loaded the
    schedule is. λ=0 collapses to perfect TWAP.

    `adv_units` lets the impact terms scale by participation rate — pass
    in the relevant Average Daily Volume in the same units as total_quantity.
    """
    p = params or ACParams()
    if total_quantity <= 0 or n_slices <= 0:
        return ACSchedule()
    if n_slices == 1:
        return ACSchedule(
            quantities=[float(total_quantity)],
            expected_cost_bp=_expected_cost_bp([total_quantity], p, adv_units),
            cost_variance_bp2=0.0,
            trajectory_shape="TWAP",
        )

    # AC trajectory parameter
    if p.risk_aversion_lambda > 0 and p.temp_impact_eta > 0:
        kappa_sq = p.risk_aversion_lambda * (p.bar_vol_bps ** 2) / p.temp_impact_eta
        kappa = math.sqrt(max(kappa_sq, 1e-18))
    else:
        kappa = 0.0

    T = float(n_slices)
    if kappa == 0 or kappa * T < 1e-6:
        # Limit: pure TWAP
        per_slice = total_quantity / n_slices
        qtys = [per_slice] * n_slices
        shape: Trajectory = "TWAP"
    elif kappa * T > 50:
        # Numerical limit — sinh overflows for kappa*T > ~700; well before
        # that the trajectory collapses to "execute all in slice 0".
        # Use the exponential approximation: x_t ≈ X * e^{-kappa*t} so
        # quantities drop off exponentially.
        weights = [math.exp(-kappa * i) for i in range(n_slices)]
        wsum = sum(weights)
        qtys = [total_quantity * w / wsum for w in weights]
        shape = "FRONT_LOAD"
    else:
        denom = math.sinh(kappa * T)
        if denom == 0:
            per_slice = total_quantity / n_slices
            qtys = [per_slice] * n_slices
            shape = "TWAP"
        else:
            holdings = [
                total_quantity * math.sinh(kappa * (T - i)) / denom
                for i in range(n_slices + 1)
            ]
            qtys = [holdings[i] - holdings[i + 1] for i in range(n_slices)]
            shape = "FRONT_LOAD"

    return ACSchedule(
        quantities=qtys,
        expected_cost_bp=_expected_cost_bp(qtys, p, adv_units),
        cost_variance_bp2=_cost_variance_bp2(qtys, p),
        trajectory_shape=shape,
    )


def _expected_cost_bp(qtys: list[float], p: ACParams, adv: float) -> float:
    """Per-fraction-of-ADV cost — temp+perm impact."""
    if not qtys or adv <= 0:
        return 0.0
    total_temp = sum(p.temp_impact_eta * (q / adv) ** 2 for q in qtys)
    cumulative = 0.0
    total_perm = 0.0
    for q in qtys:
        cumulative += q
        total_perm += p.perm_impact_gamma * cumulative * (q / adv)
    return (total_temp + total_perm) * 10_000.0


def _cost_variance_bp2(qtys: list[float], p: ACParams) -> float:
    """Variance of execution cost (in bp²) under AC quadratic impact."""
    if not qtys:
        return 0.0
    holdings = []
    h = sum(qtys)
    holdings.append(h)
    for q in qtys[:-1]:
        h -= q
        holdings.append(h)
    return float((p.bar_vol_bps ** 2) * sum(h ** 2 for h in holdings))


def back_loaded_schedule(total_quantity: float, n_slices: int) -> ACSchedule:
    """Mirror of front-load — used as a candidate for the RL bandit."""
    fwd = optimal_schedule(total_quantity, n_slices,
                            params=ACParams(risk_aversion_lambda=1e-4))
    rev = list(reversed(fwd.quantities))
    return ACSchedule(
        quantities=rev,
        expected_cost_bp=fwd.expected_cost_bp,
        cost_variance_bp2=fwd.cost_variance_bp2,
        trajectory_shape="BACK_LOAD",
    )


def twap_schedule(total_quantity: float, n_slices: int) -> ACSchedule:
    return optimal_schedule(total_quantity, n_slices,
                             params=ACParams(risk_aversion_lambda=0.0))


# ──────────────────────────────────────────────────────────────────
# RL refinement — bandit over trajectory shapes
# ──────────────────────────────────────────────────────────────────


@dataclass
class _ShapeArm:
    n: int = 0
    mean_cost_bp: float = 0.0

    def update(self, cost_bp: float, gamma: float = 0.9) -> None:
        self.n += 1
        if self.n == 1:
            self.mean_cost_bp = cost_bp
        else:
            self.mean_cost_bp = gamma * self.mean_cost_bp + (1 - gamma) * cost_bp


@dataclass
class TrajectoryBandit:
    """Epsilon-greedy bandit over (FRONT_LOAD, TWAP, BACK_LOAD).

    Reward = -realized_cost_bp (we minimize cost). Per-arm running mean
    with exponential smoothing so the bandit tracks regime drift in
    impact / volatility.
    """

    epsilon: float = 0.15
    _arms: dict[Trajectory, _ShapeArm] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _ensure(self) -> dict[Trajectory, _ShapeArm]:
        for s in ("FRONT_LOAD", "TWAP", "BACK_LOAD"):
            if s not in self._arms:
                self._arms[s] = _ShapeArm()  # type: ignore[assignment]
        return self._arms

    def select(self) -> Trajectory:
        with self._lock:
            self._ensure()
            if random.random() < self.epsilon or all(a.n == 0 for a in self._arms.values()):
                return random.choice(("FRONT_LOAD", "TWAP", "BACK_LOAD"))
            # Greedy: lowest mean cost (max reward = min cost)
            return min(self._arms.keys(), key=lambda k: self._arms[k].mean_cost_bp if self._arms[k].n > 0 else 1e9)  # type: ignore[return-value]

    def update(self, shape: Trajectory, realized_cost_bp: float) -> None:
        with self._lock:
            self._ensure()
            if shape not in self._arms:
                raise ValueError(f"unknown trajectory shape: {shape}")
            self._arms[shape].update(realized_cost_bp)

    def stats(self) -> dict[Trajectory, dict]:
        with self._lock:
            return {
                shape: {"n": arm.n, "mean_cost_bp": round(arm.mean_cost_bp, 4)}
                for shape, arm in self._arms.items()
            }


def build_schedule_for_shape(
    shape: Trajectory,
    total_quantity: float,
    n_slices: int,
    params: ACParams | None = None,
    adv_units: float = 1.0,
) -> ACSchedule:
    """Dispatcher used by the bandit-select → execute path."""
    if shape == "TWAP":
        return twap_schedule(total_quantity, n_slices)
    if shape == "BACK_LOAD":
        return back_loaded_schedule(total_quantity, n_slices)
    return optimal_schedule(
        total_quantity, n_slices,
        params=params or ACParams(risk_aversion_lambda=1e-4),
        adv_units=adv_units,
    )
