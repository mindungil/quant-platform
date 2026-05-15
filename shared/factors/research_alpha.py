"""Research-backed alpha factors with documented edge in crypto markets.

Sources:
- Liu, Tsyvinski, Wu (JF 2022) "Common Risk Factors in Cryptocurrency"
- Liu & Tsyvinski (RFS 2021) "Risks and Returns of Cryptocurrency"
- Koijen, Moskowitz, Pedersen (JFE 2018) "Carry"
- Moreira & Muir (JF 2017) "Volatility-Managed Portfolios"
- Cont, Kukanov, Stoikov (J. Fin. Econometrics 2014) "The Price Impact of Order Book Events"

These factors are designed to be ENSEMBLE additions — they each have documented
out-of-sample edge in published peer-reviewed work.  All return values are
normalized to [-1, 1] and return 0.0 on missing data (never crash).
"""
from __future__ import annotations
import math
from shared.factors.base import Factor


# ---------------------------------------------------------------------------
# 1. Funding Carry (annualized) — Koijen et al. 2018, applied to crypto perps
# ---------------------------------------------------------------------------
class FundingCarryAnnualized(Factor):
    """Annualized funding rate as a delta-neutral carry signal.

    Edge: perp funding has historically delivered Sharpe 2-4 in cash-and-carry.
    High positive funding -> longs are paying to hold -> short perp / long spot
    is profitable -> bearish for outright long position (contrarian).

    Formula: carry_apr = funding_8h * 3 * 365
    Threshold: > 10% APR is crowded long, < -10% APR is crowded short
    """

    def __init__(self):
        super().__init__(
            name="funding_carry_apr",
            category="derivatives",
            description="Annualized funding (8h * 3 * 365); contrarian at extremes",
        )

    def compute(self, features: dict) -> float:
        funding = self._safe_get(features, "funding_rate")
        if funding == 0.0:
            return 0.0
        # 8h funding * 3 per day * 365 -> annualized rate
        carry_apr = funding * 3.0 * 365.0
        # Contrarian: 30% APR -> -1.0, -30% -> +1.0
        return self._tanh_norm(-carry_apr, 0.30)


# ---------------------------------------------------------------------------
# 2. Time-Series Momentum (Liu & Tsyvinski 2021) — t-stat > 3 OOS in crypto
# ---------------------------------------------------------------------------
class TimeSeriesMomentum1W(Factor):
    """1-week time-series momentum.

    Edge: log(P_t / P_{t-7}) had t-stat > 3 across 1700+ coins (Liu & Tsyvinski).
    Sharpe ~1.5 standalone, decorrelated from technical momentum.
    """

    def __init__(self):
        super().__init__(
            name="ts_momentum_1w",
            category="momentum",
            description="log(close / close_7d_ago); +0.07 (~7%) -> +1.0",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        close_7d = self._safe_get(features, "close_7d_ago")
        if close <= 0 or close_7d <= 0:
            return 0.0
        log_return = math.log(close / close_7d)
        # 7% weekly move -> ~+1.0
        return self._tanh_norm(log_return, 0.07)


class TimeSeriesMomentum4W(Factor):
    """4-week time-series momentum (medium-term trend).

    Edge: monthly momentum Sharpe ~1.0 in crypto, persistent through 2023.
    """

    def __init__(self):
        super().__init__(
            name="ts_momentum_4w",
            category="momentum",
            description="log(close / close_28d_ago); +0.20 (~20%) -> +1.0",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        close_28d = self._safe_get(features, "close_28d_ago")
        if close <= 0 or close_28d <= 0:
            return 0.0
        log_return = math.log(close / close_28d)
        return self._tanh_norm(log_return, 0.20)


class TimeSeriesReversal1D(Factor):
    """1-day reversal — short-term mean-reversion.

    Edge: 1-day reversal documented in Jegadeesh 1990 and confirmed in crypto
    by Shen, Urquhart, Wang (2020). Strong overnight returns tend to revert.
    """

    def __init__(self):
        super().__init__(
            name="ts_reversal_1d",
            category="reversion",
            description="-log(close / close_1d_ago); contrarian at extremes",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        close_1d = self._safe_get(features, "close_1d_ago")
        if close <= 0 or close_1d <= 0:
            return 0.0
        log_return = math.log(close / close_1d)
        # Contrarian: +5% daily move -> -1.0 (sell)
        return self._tanh_norm(-log_return, 0.05)


# ---------------------------------------------------------------------------
# 3. Volatility-Managed Signal (Moreira & Muir 2017)
# ---------------------------------------------------------------------------
class VolatilityManagedTrend(Factor):
    """Trend signal scaled inversely by realized volatility.

    Edge: volatility scaling raises Sharpe by 0.2-0.5 (Moreira & Muir 2017,
    Harvey et al. 2018). Effect is *stronger* in crypto due to vol clustering.

    Logic: when ATR is low relative to price, trust the trend more (signal
    is louder relative to noise). When ATR is high, dampen the signal.
    """

    def __init__(self):
        super().__init__(
            name="vol_managed_trend",
            category="volatility",
            description="(close - ema21) / ema21 scaled by inverse vol",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        ema21 = self._safe_get(features, "ema_21")
        atr = self._safe_get(features, "atr_14")
        if close <= 0 or ema21 <= 0 or atr <= 0:
            return 0.0
        # Trend strength as fraction of price
        trend_pct = (close - ema21) / ema21
        # Realized vol proxy as fraction of price
        vol_pct = atr / close
        if vol_pct <= 0:
            return 0.0
        # Risk-adjusted trend (Sharpe-like ratio per bar)
        risk_adj = trend_pct / vol_pct
        # Cap at ±2 risk-adjusted units
        return self._tanh_norm(risk_adj, 1.5)


# ---------------------------------------------------------------------------
# 4. Order Flow Imbalance proxy (Cont, Kukanov, Stoikov 2014)
# ---------------------------------------------------------------------------
class OrderFlowImbalanceProxy(Factor):
    """OFI proxy using OBV-derived flow.

    Edge: Cont/Kukanov/Stoikov 2014 show OFI explains 65%+ of 1-min price
    changes. Real OFI requires L1 tick data; we approximate from OBV slope
    relative to volume baseline.
    """

    def __init__(self):
        super().__init__(
            name="order_flow_imbalance",
            category="technical",
            description="OBV slope / volume baseline as flow imbalance proxy",
        )

    def compute(self, features: dict) -> float:
        obv = self._safe_get(features, "obv")
        volume = self._safe_get(features, "volume")
        obv_prev = self._safe_get(features, "obv_prev")
        if volume <= 0:
            return 0.0
        # Use delta if prev available, else use OBV sign
        if obv_prev != 0.0:
            obv_delta = obv - obv_prev
            # Normalize delta by recent volume
            return self._tanh_norm(obv_delta / volume, 1.0)
        # Fallback: sign of OBV relative to its magnitude
        if obv == 0.0:
            return 0.0
        return self._tanh_norm(obv / (abs(obv) + volume * 100.0), 0.5)


# ---------------------------------------------------------------------------
# 5. Realized Volatility Regime (high-vol -> reduce conviction)
# ---------------------------------------------------------------------------
class RealizedVolRegime(Factor):
    """Realized vol regime as a confidence dampener.

    Edge: in high-vol regimes, mean signals become less reliable. Returning
    a SMALL positive value in calm regimes and ~0 in stormy regimes acts as
    a confidence multiplier when summed in the ensemble.
    """

    def __init__(self):
        super().__init__(
            name="vol_regime_calm",
            category="volatility",
            description="+score in calm vol regimes, 0 in stormy",
        )

    def compute(self, features: dict) -> float:
        atr = self._safe_get(features, "atr_14")
        close = self._safe_get(features, "close")
        if close <= 0 or atr <= 0:
            return 0.0
        vol_pct = atr / close
        # Crypto BTC: typical ATR/close is 1.5-3%; calm < 1.5%, stormy > 5%
        if vol_pct >= 0.05:
            return -0.5  # explicit caution
        if vol_pct <= 0.015:
            return 0.3  # mild confidence boost
        # Linear in between
        return 0.3 - ((vol_pct - 0.015) / 0.035) * 0.8


RESEARCH_ALPHA_FACTORS = [
    FundingCarryAnnualized(),
    TimeSeriesMomentum1W(),
    TimeSeriesMomentum4W(),
    TimeSeriesReversal1D(),
    VolatilityManagedTrend(),
    OrderFlowImbalanceProxy(),
    RealizedVolRegime(),
]
