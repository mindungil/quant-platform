"""Mean-reversion formulas — best in sideways/ranging markets.

Includes adaptive RSI thresholds, z-score reversion, VWAP velocity,
Bollinger %B with Keltner confirmation, simplified Hurst exponent,
and volume divergence detection.
"""
from __future__ import annotations

import math
from collections import deque

from shared.formulas.base import BaseFormula, FormulaResult
from shared.formulas.registry import formula_registry


# ---------------------------------------------------------------------------
# helpers shared across reversion formulas
# ---------------------------------------------------------------------------


def _percentile(data: deque | list, pct: float) -> float:
    """Simple linear-interpolation percentile, no numpy needed."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = pct * (len(s) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ===================================================================
# 1. Bollinger %B + Keltner Confirmation
# ===================================================================


class BollingerMeanReversionFormula(BaseFormula):
    name = "mean_reversion_bb"
    description = "Bollinger %B contrarian with Keltner-confirmed bandwidth regime"
    best_regime = "sideways"
    required_indicators = [
        "close",
        "bb_upper",
        "bb_lower",
        "sma_20",
        "atr_14",
        "volume",
        "obv",
    ]

    KELTNER_MULT = 1.5
    BW_WINDOW = 100
    VOL_WINDOW = 20

    def __init__(self) -> None:
        super().__init__()
        self._bw_history: deque[float] = deque(maxlen=self.BW_WINDOW)
        self._vol_history: deque[float] = deque(maxlen=self.VOL_WINDOW)

    def compute(self, features: dict) -> FormulaResult:
        close = features.get("close")
        bb_upper = features.get("bb_upper")
        bb_lower = features.get("bb_lower")
        sma_20 = features.get("sma_20")
        atr = features.get("atr_14") or 1.0
        volume = features.get("volume")

        if close is None or bb_upper is None or bb_lower is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        bb_range = bb_upper - bb_lower
        if bb_range <= 0:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        # --- %B ---
        pct_b = (close - bb_lower) / bb_range

        # --- bandwidth tracking ---
        bb_width = bb_range / sma_20 if sma_20 and sma_20 > 0 else 0.0
        self._bw_history.append(bb_width)

        # --- Keltner confirmation: BB inside KC means low-vol regime (good for MR) ---
        ema_20 = features.get("ema_20") or sma_20 or close
        kc_upper = ema_20 + self.KELTNER_MULT * atr
        kc_lower = ema_20 - self.KELTNER_MULT * atr
        keltner_inside = bb_upper < kc_upper and bb_lower > kc_lower
        keltner_boost = 1.25 if keltner_inside else 1.0

        # --- volume divergence: price at extreme but volume declining ---
        vol_div_mult = 1.0
        if volume is not None:
            self._vol_history.append(volume)
            if len(self._vol_history) >= 10:
                avg_vol = sum(self._vol_history) / len(self._vol_history)
                recent_avg = sum(list(self._vol_history)[-5:]) / 5
                if avg_vol > 0 and recent_avg < avg_vol * 0.8:
                    # volume declining — exhaustion, strengthens MR
                    vol_div_mult = 1.2

        # --- contrarian score ---
        # pct_b near 0 = oversold -> buy (+), near 1 = overbought -> sell (-)
        raw_score = -(pct_b - 0.5) * 2  # [1, -1]
        score = _clamp(raw_score * keltner_boost * vol_div_mult)

        # --- confidence: higher at extremes, boosted by Keltner confirmation ---
        extremity = abs(pct_b - 0.5) * 2  # 0 at midband, 1 at bands
        confidence = _clamp(extremity * keltner_boost * vol_div_mult, 0.0, 1.0)

        return FormulaResult(
            score=score,
            confidence=confidence,
            components={
                "pct_b": round(pct_b, 4),
                "bb_width": round(bb_width, 6),
                "keltner_inside": keltner_inside,
                "vol_divergence_mult": round(vol_div_mult, 2),
            },
            formula_name=self.name,
        )


# ===================================================================
# 2. VWAP Reversion with Velocity
# ===================================================================


class VWAPReversionFormula(BaseFormula):
    name = "vwap_reversion"
    description = "VWAP distance + convergence velocity as mean-reversion signal"
    best_regime = "sideways"
    required_indicators = ["close", "vwap", "atr_14"]

    DISTANCE_WINDOW = 10  # bars to track for velocity

    def __init__(self) -> None:
        super().__init__()
        self._dist_history: deque[float] = deque(maxlen=self.DISTANCE_WINDOW)

    def compute(self, features: dict) -> FormulaResult:
        close = features.get("close")
        vwap = features.get("vwap")
        atr = features.get("atr_14") or 1.0

        if close is None or vwap is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        distance = close - vwap
        norm_dist = distance / atr if atr > 0 else 0.0
        self._dist_history.append(norm_dist)

        # --- velocity: rate of convergence toward VWAP ---
        velocity = 0.0
        if len(self._dist_history) >= 3:
            # positive velocity = moving toward VWAP (distance shrinking)
            old = self._dist_history[0]
            velocity = old - norm_dist  # positive if |distance| is decreasing

        # --- base contrarian signal ---
        base_score = -math.tanh(norm_dist / 2.0)

        # --- velocity adjustment ---
        # If already converging fast, signal is weaker (move already happening)
        # If diverging or stalling at extreme, signal is stronger
        if abs(norm_dist) > 1.0:
            # At extreme distance
            if velocity > 0:
                # Already converging — reduce signal (late entry)
                vel_adj = max(0.6, 1.0 - velocity * 0.15)
            else:
                # Still diverging at extreme — stronger reversion expected
                vel_adj = min(1.3, 1.0 + abs(velocity) * 0.15)
        else:
            vel_adj = 1.0

        score = _clamp(base_score * vel_adj)
        confidence = _clamp(min(abs(norm_dist) / 2.5, 1.0) * vel_adj, 0.0, 1.0)

        return FormulaResult(
            score=score,
            confidence=confidence,
            components={
                "vwap_distance": round(distance, 4),
                "norm_distance": round(norm_dist, 4),
                "velocity": round(velocity, 4),
                "velocity_adj": round(vel_adj, 3),
            },
            formula_name=self.name,
        )


# ===================================================================
# 3. RSI Adaptive Reversion with Hurst & Volume Divergence
# ===================================================================


class RSIDivergenceFormula(BaseFormula):
    name = "rsi_divergence"
    description = (
        "Adaptive RSI thresholds with Hurst regime filter and volume divergence"
    )
    best_regime = "reversal"
    required_indicators = ["rsi_14", "close", "volume", "sma_50", "atr_14"]

    RSI_WINDOW = 100  # rolling window for adaptive thresholds
    CLOSE_WINDOW = 50  # for simplified Hurst estimation
    VOL_WINDOW = 20

    def __init__(self) -> None:
        super().__init__()
        self._rsi_history: deque[float] = deque(maxlen=self.RSI_WINDOW)
        self._close_history: deque[float] = deque(maxlen=self.CLOSE_WINDOW)
        self._vol_history: deque[float] = deque(maxlen=self.VOL_WINDOW)

    # --- simplified Hurst exponent via rescaled-range ---

    def _estimate_hurst(self) -> float | None:
        """Simplified Hurst exponent from log-return variance scaling.

        H < 0.5 = mean-reverting, H > 0.5 = trending, H ~ 0.5 = random walk.
        Uses variance ratio of 2 timescales as a fast proxy.
        """
        prices = list(self._close_history)
        n = len(prices)
        if n < 20:
            return None

        # log returns
        returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, n) if prices[i - 1] > 0]
        if len(returns) < 20:
            return None

        # variance at lag=1
        var1 = sum(r * r for r in returns) / len(returns)
        if var1 <= 0:
            return None

        # variance at lag=5 (sum of 5-bar returns)
        lag = 5
        long_returns = []
        for i in range(0, len(returns) - lag + 1, lag):
            lr = sum(returns[i : i + lag])
            long_returns.append(lr)
        if len(long_returns) < 4:
            return None

        var5 = sum(r * r for r in long_returns) / len(long_returns)
        if var5 <= 0:
            return None

        # H = log(var_ratio) / log(lag)
        # For random walk: var5 = lag * var1, so ratio = lag, H = 0.5
        hurst = math.log(var5 / var1) / (2 * math.log(lag))
        return _clamp(hurst, 0.0, 1.0)

    def _adaptive_thresholds(self) -> tuple[float, float]:
        """Compute adaptive oversold/overbought from rolling RSI percentiles.

        Bottom 10% = oversold threshold, top 10% = overbought threshold.
        Falls back to 30/70 if not enough data.
        """
        if len(self._rsi_history) < 30:
            return 30.0, 70.0
        oversold = _percentile(self._rsi_history, 0.10)
        overbought = _percentile(self._rsi_history, 0.90)
        # safety clamps — never wider than 20/80, never narrower than 35/65
        oversold = _clamp(oversold, 20.0, 35.0)
        overbought = _clamp(overbought, 65.0, 80.0)
        return oversold, overbought

    def compute(self, features: dict) -> FormulaResult:
        rsi = features.get("rsi_14")
        close = features.get("close")
        volume = features.get("volume")
        sma_50 = features.get("sma_50")
        atr = features.get("atr_14") or 1.0

        if rsi is None:
            return FormulaResult(score=0.0, confidence=0.0, formula_name=self.name)

        # --- update history buffers ---
        self._rsi_history.append(rsi)
        if close is not None:
            self._close_history.append(close)
        if volume is not None:
            self._vol_history.append(volume)

        # --- adaptive thresholds ---
        oversold_th, overbought_th = self._adaptive_thresholds()

        # --- z-score of price relative to SMA(50) ---
        z_score = 0.0
        if close is not None and sma_50 is not None and atr > 0:
            z_score = (close - sma_50) / atr

        # --- base RSI signal ---
        if rsi < oversold_th:
            # depth below oversold, normalized by the threshold width
            depth = (oversold_th - rsi) / oversold_th
            score = min(depth * 1.5, 1.0)  # buy signal
        elif rsi > overbought_th:
            depth = (rsi - overbought_th) / (100 - overbought_th)
            score = -min(depth * 1.5, 1.0)  # sell signal
        else:
            score = 0.0

        # --- Hurst regime filter ---
        hurst = self._estimate_hurst()
        hurst_mult = 1.0
        if hurst is not None:
            if hurst < 0.4:
                # strongly mean-reverting regime — boost
                hurst_mult = 1.0 + (0.4 - hurst) * 2.5  # up to 2.0x at H=0.0
                hurst_mult = min(hurst_mult, 2.0)
            elif hurst > 0.6:
                # trending regime — dampen MR signals
                hurst_mult = max(0.3, 1.0 - (hurst - 0.6) * 2.5)

        # --- z-score confirmation ---
        # If z-score agrees with RSI signal, boost; if disagrees, dampen
        z_mult = 1.0
        if abs(z_score) > 1.0:
            if (score > 0 and z_score < -1.0) or (score < 0 and z_score > 1.0):
                # price is far from SMA in direction consistent with RSI extreme
                z_mult = 1.2
            elif (score > 0 and z_score > 1.0) or (score < 0 and z_score < -1.0):
                # RSI says oversold but price is above SMA — contradiction
                z_mult = 0.7

        # --- volume divergence ---
        vol_div_mult = 1.0
        if len(self._vol_history) >= 10 and abs(score) > 0:
            avg_vol = sum(self._vol_history) / len(self._vol_history)
            recent_5 = list(self._vol_history)[-5:]
            recent_avg = sum(recent_5) / 5
            if avg_vol > 0 and recent_avg < avg_vol * 0.75:
                # Volume declining at price extreme = exhaustion -> stronger MR
                vol_div_mult = 1.25

        # --- composite ---
        final_score = _clamp(score * hurst_mult * z_mult * vol_div_mult)

        # --- confidence ---
        base_conf = abs(score)
        conf = base_conf * min(hurst_mult, 1.5) / 1.5 * z_mult * vol_div_mult
        confidence = _clamp(conf, 0.0, 1.0)

        return FormulaResult(
            score=final_score,
            confidence=confidence,
            components={
                "rsi": rsi,
                "oversold_th": round(oversold_th, 1),
                "overbought_th": round(overbought_th, 1),
                "z_score": round(z_score, 3),
                "hurst": round(hurst, 3) if hurst is not None else None,
                "hurst_mult": round(hurst_mult, 3),
                "z_mult": round(z_mult, 2),
                "vol_div_mult": round(vol_div_mult, 2),
            },
            formula_name=self.name,
        )


formula_registry.register(BollingerMeanReversionFormula())
formula_registry.register(VWAPReversionFormula())
formula_registry.register(RSIDivergenceFormula())
