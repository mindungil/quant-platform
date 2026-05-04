"""Almgren-Chriss optimal execution model.

Institutional desks schedule large orders over time to trade off **market
impact** (cost of consuming liquidity) against **timing risk** (cost of
price drift while you wait). The canonical framework is Almgren-Chriss
(2000): pick the trajectory x_0, x_1, …, x_N that minimizes

    E[cost] + λ · Var[cost]

where cost = permanent impact · total size + temporary impact per trade
+ price drift × unexecuted inventory, and λ is the trader's risk aversion.

For constant linear impact and σ² volatility, the optimal trajectory is
the hyperbolic-cosine schedule with decay parameter κ = sqrt(λ σ² / η̃).

Pure numpy. Returns per-slice notional to execute.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class ACParams:
    total_quantity: float       # positive = buy, negative = sell
    total_time: float = 1.0     # execution horizon (hours)
    n_slices: int = 12          # number of child orders
    sigma: float = 0.02         # per-hour volatility (fractional)
    gamma: float = 1e-7         # permanent impact coefficient (per unit)
    eta: float = 2.5e-6         # temporary impact coefficient (per unit per time)
    epsilon: float = 0.0        # fixed bid-ask spread cost per unit
    risk_aversion: float = 1e-6 # λ — higher = faster execution
    min_slice_notional: float = 10.0  # drop slices below this


@dataclass
class ACSchedule:
    slice_sizes: np.ndarray      # quantity per slice (signed)
    slice_times: np.ndarray      # time stamp of each slice
    expected_cost: float
    cost_variance: float
    half_life: float             # 1/κ — intuitive "decay" parameter
    urgency: str                 # "slow" / "normal" / "fast"


def optimal_trajectory(params: ACParams) -> ACSchedule:
    """Solve the Almgren-Chriss problem for linear impact.

    x_k = X · sinh(κ (T - t_k)) / sinh(κ T)
    n_k = x_{k-1} - x_k is the slice to execute at step k.
    """
    X = params.total_quantity
    T = params.total_time
    N = max(params.n_slices, 1)
    tau = T / N  # slice duration
    sigma = params.sigma
    gamma = params.gamma
    eta = params.eta
    lam = params.risk_aversion

    # Effective temporary impact coefficient (Almgren-Chriss eq. after
    # subtracting the permanent portion that's not pathwise controllable):
    eta_hat = eta - 0.5 * gamma * tau
    if eta_hat <= 0:
        eta_hat = eta
    # κ solves  2 (1 - cosh(κτ)) / τ² = −λ σ² / η̂
    # Small-τ closed form: κ ≈ sqrt(λ σ² / η̂)
    kappa_sq = max(lam * sigma ** 2 / eta_hat, 0.0)
    kappa = math.sqrt(kappa_sq) if kappa_sq > 0 else 0.0

    times = np.linspace(0, T, N + 1)
    if kappa * T > 1e-6:
        remaining = X * np.sinh(kappa * (T - times)) / math.sinh(kappa * T)
    else:
        # Risk-neutral limit → linear (TWAP) execution
        remaining = X * (1 - times / T)

    slice_sizes = -np.diff(remaining)  # positive slices when X > 0

    # Drop dust
    mask = np.abs(slice_sizes) >= params.min_slice_notional
    slice_sizes = slice_sizes[mask]
    slice_times = times[1:][mask]

    # Expected cost ≈ 0.5 γ X² + ε |X| + η̂/τ · Σ n_k²
    # Variance ≈ σ² · Σ τ · x_k²   (using the average remaining per interval)
    expected_cost = (
        0.5 * gamma * X ** 2
        + params.epsilon * abs(X)
        + eta_hat / max(tau, 1e-9) * float(np.sum(slice_sizes ** 2))
    )
    mid_remaining = 0.5 * (remaining[:-1] + remaining[1:])
    cost_variance = sigma ** 2 * tau * float(np.sum(mid_remaining ** 2))

    # Urgency label — useful for pre-trade TCA reports.
    if kappa * T < 0.5:
        urgency = "slow"
    elif kappa * T < 2.0:
        urgency = "normal"
    else:
        urgency = "fast"

    half_life = 1.0 / kappa if kappa > 0 else float("inf")

    return ACSchedule(
        slice_sizes=slice_sizes,
        slice_times=slice_times,
        expected_cost=float(expected_cost),
        cost_variance=float(cost_variance),
        half_life=float(half_life),
        urgency=urgency,
    )


def implementation_shortfall(
    arrival_price: float,
    fills: list[tuple[float, float]],  # (price, quantity)
    benchmark: str = "arrival",
) -> dict:
    """Post-trade TCA: measure slippage vs arrival / VWAP.

    Returns total notional traded, VWAP, shortfall in bps (signed — positive
    means adverse vs benchmark, i.e. we paid more than arrival for a buy).
    """
    if not fills:
        return {"error": "no_fills"}
    qty = sum(abs(q) for _, q in fills)
    if qty <= 0:
        return {"error": "zero_qty"}
    vwap = sum(p * abs(q) for p, q in fills) / qty
    side = 1 if sum(q for _, q in fills) > 0 else -1
    if benchmark == "arrival":
        ref = arrival_price
    else:
        ref = vwap
    # bps adverse for a buy: (vwap - arrival) / arrival · 1e4
    shortfall_bps = side * (vwap - ref) / ref * 1e4
    return {
        "qty": qty,
        "vwap": round(vwap, 6),
        "arrival_price": arrival_price,
        "shortfall_bps": round(float(shortfall_bps), 2),
        "n_fills": len(fills),
    }
