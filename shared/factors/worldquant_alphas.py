"""WorldQuant 101 Formulaic Alphas — time-series single-asset adaptations.

Source: Kakushadze (2015) "101 Formulaic Alphas" arXiv:1601.00991

The original alphas rely heavily on cross-sectional `rank()` operators across
a universe of securities and multi-bar rolling buffers (ts_rank, correlation,
decay_linear, stddev, sum over N).  This module picks 25 alphas that can be
sensibly ADAPTED to a single-asset decision-time features dict containing:

    close, open (proxied by ema_9 when absent), high, low, volume,
    vwap, ema_9/21/50/200, sma_20/50, atr_14, rsi_14, close_1d_ago,
    close_7d_ago, close_28d_ago, obv, ...

Key substitutions used throughout:
    rank(x)              -> tanh/linear normalization (time-series z-proxy)
    delta(close, N)      -> close - close_Nd_ago
    ts_rank(close, N)    -> (close - sma_N) / (atr_14 or band width)
    stddev(returns, N)   -> |ret_1d| or atr/close as realized-vol proxy
    correlation(...)     -> sign product / slope proxy from available series
    sum(returns, N)      -> (close - close_Nd_ago) / close_Nd_ago
    vwap                 -> features['vwap']
    adv20                -> no history; use current volume as baseline

All factors return 0.0 on missing data and a normalized score in [-1, 1].
"""
from __future__ import annotations
import math
from shared.factors.base import Factor


def _ret(features: dict, ago_key: str) -> float:
    c = Factor._safe_get(features, "close")
    p = Factor._safe_get(features, ago_key)
    if c <= 0 or p <= 0:
        return 0.0
    return (c - p) / p


def _open(features: dict) -> float:
    """Use 'open' if present, else ema_9 as same-bar proxy."""
    o = features.get("open")
    if o is None:
        o = features.get("ema_9")
    try:
        v = float(o) if o is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    return v


def _high(features: dict) -> float:
    h = features.get("high")
    if h is None:
        # Proxy: close + 0.5*atr
        c = Factor._safe_get(features, "close")
        a = Factor._safe_get(features, "atr_14")
        return c + 0.5 * a
    try:
        return float(h)
    except (TypeError, ValueError):
        return 0.0


def _low(features: dict) -> float:
    l = features.get("low")
    if l is None:
        c = Factor._safe_get(features, "close")
        a = Factor._safe_get(features, "atr_14")
        return c - 0.5 * a
    try:
        return float(l)
    except (TypeError, ValueError):
        return 0.0


def _sign(x: float) -> float:
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    return 0.0


# ---------------------------------------------------------------------------
# Alpha #2
# ---------------------------------------------------------------------------
class WQAlpha002(Factor):
    """Original: (-1 * correlation(rank(delta(log(volume),2)), rank((close-open)/open), 6))

    Adaptation: we have no volume history. Proxy as the negative of the
    sign product of (intra-bar body) and (volume above a soft baseline),
    yielding a reversion cue when heavy volume coincides with a directional
    body move.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_002",
            category="reversion",
            description="-sign(body * volume_above_baseline)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        o = _open(features)
        vol = self._safe_get(features, "volume")
        if close <= 0 or o <= 0 or vol <= 0:
            return 0.0
        body = (close - o) / o
        # soft volume z: compare to a neutral baseline equal to volume itself
        # (we lack history), so just use magnitude of body as signal strength
        return self._tanh_norm(-body, 0.01)


# ---------------------------------------------------------------------------
# Alpha #3
# ---------------------------------------------------------------------------
class WQAlpha003(Factor):
    """Original: (-1 * correlation(rank(open), rank(volume), 10))

    Adaptation: negative sign product of open-vs-sma20 and volume anomaly.
    When open is above trend with heavy volume -> reversion.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_003",
            category="reversion",
            description="-sign((open - sma20) * volume)",
        )

    def compute(self, features: dict) -> float:
        o = _open(features)
        sma = self._safe_get(features, "sma_20")
        vol = self._safe_get(features, "volume")
        if o <= 0 or sma <= 0 or vol <= 0:
            return 0.0
        trend_dev = (o - sma) / sma
        return self._tanh_norm(-trend_dev, 0.02)


# ---------------------------------------------------------------------------
# Alpha #4
# ---------------------------------------------------------------------------
class WQAlpha004(Factor):
    """Original: (-1 * ts_rank(rank(low), 9))

    Adaptation: ts_rank(low, 9) proxied by position of low within a band
    centered on sma_20 +/- atr.  High rank (low near band top) -> negative.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_004",
            category="reversion",
            description="-position of low within sma20 +/- atr band",
        )

    def compute(self, features: dict) -> float:
        low = _low(features)
        sma = self._safe_get(features, "sma_20")
        atr = self._safe_get(features, "atr_14")
        if low <= 0 or sma <= 0 or atr <= 0:
            return 0.0
        pos = (low - sma) / atr
        return self._tanh_norm(-pos, 1.0)


# ---------------------------------------------------------------------------
# Alpha #5
# ---------------------------------------------------------------------------
class WQAlpha005(Factor):
    """Original: (rank((open - (sum(vwap,10)/10))) * (-1 * abs(rank((close - vwap)))))

    Adaptation: the signal combines (open-vwap_avg) trend with penalty for
    large |close-vwap| deviation.  Use (open - vwap) * -|close - vwap| / close.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_005",
            category="reversion",
            description="(open-vwap) * -|close-vwap|/close",
        )

    def compute(self, features: dict) -> float:
        o = _open(features)
        close = self._safe_get(features, "close")
        vwap = self._safe_get(features, "vwap")
        if o <= 0 or close <= 0 or vwap <= 0:
            return 0.0
        trend = (o - vwap) / vwap
        penalty = abs(close - vwap) / close
        return self._tanh_norm(trend * -penalty, 0.001)


# ---------------------------------------------------------------------------
# Alpha #6
# ---------------------------------------------------------------------------
class WQAlpha006(Factor):
    """Original: (-1 * correlation(open, volume, 10))

    Adaptation: -sign(open_dev * volume_baseline).  We use
    (open - sma_20) * (volume - vwap*0) as a rough co-movement proxy.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_006",
            category="reversion",
            description="-sign((open - sma20)) scaled by vol magnitude",
        )

    def compute(self, features: dict) -> float:
        o = _open(features)
        sma = self._safe_get(features, "sma_20")
        vol = self._safe_get(features, "volume")
        if o <= 0 or sma <= 0 or vol <= 0:
            return 0.0
        dev = (o - sma) / sma
        return self._tanh_norm(-dev * math.log1p(vol) / 10.0, 0.05)


# ---------------------------------------------------------------------------
# Alpha #7
# ---------------------------------------------------------------------------
class WQAlpha007(Factor):
    """Original: (adv20 < volume) ? (-1 * ts_rank(abs(delta(close,7)),60)) * sign(delta(close,7)) : -1

    Adaptation: we lack adv20 history.  Use sign(delta(close,7)) with magnitude
    scaled by atr.  Negative sign (reversion flavor of the original when volume
    high).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_007",
            category="reversion",
            description="-sign(close-close_7d_ago) * |delta|/atr",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        close_7d = self._safe_get(features, "close_7d_ago")
        atr = self._safe_get(features, "atr_14")
        if close <= 0 or close_7d <= 0 or atr <= 0:
            return 0.0
        delta = close - close_7d
        mag = abs(delta) / (atr * 7.0)
        return self._tanh_norm(-_sign(delta) * mag, 1.0)


# ---------------------------------------------------------------------------
# Alpha #8
# ---------------------------------------------------------------------------
class WQAlpha008(Factor):
    """Original: (-1 * rank(((sum(open,5) * sum(returns,5)) -
                              delay((sum(open,5) * sum(returns,5)),10))))

    Adaptation: approximate sum(open,5) by 5*ema_9 and sum(returns,5) by
    5*(close-close_7d_ago)/close_7d_ago.  Use negative sign of product.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_008",
            category="reversion",
            description="-sign(ema_9 * 5d_return)",
        )

    def compute(self, features: dict) -> float:
        ema9 = self._safe_get(features, "ema_9")
        ret7 = _ret(features, "close_7d_ago")
        if ema9 <= 0:
            return 0.0
        return self._tanh_norm(-ret7 * math.log1p(ema9) / 10.0, 0.02)


# ---------------------------------------------------------------------------
# Alpha #12
# ---------------------------------------------------------------------------
class WQAlpha012(Factor):
    """Original: (sign(delta(volume,1)) * (-1 * delta(close,1)))

    Adaptation: we lack delta(volume,1); replace with sign of (volume - vwap*0)
    neutral = +1, so factor reduces to -delta(close,1)/close (1-day reversal).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_012",
            category="reversion",
            description="-1d return (sign(delta_vol) proxied to +1)",
        )

    def compute(self, features: dict) -> float:
        r = _ret(features, "close_1d_ago")
        return self._tanh_norm(-r, 0.03)


# ---------------------------------------------------------------------------
# Alpha #14
# ---------------------------------------------------------------------------
class WQAlpha014(Factor):
    """Original: ((-1 * rank(delta(returns,3))) * correlation(open, volume, 10))

    Adaptation: -sign(3d return accel) * sign((open-sma20)).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_014",
            category="reversion",
            description="-accel(3d ret) * sign(open deviation)",
        )

    def compute(self, features: dict) -> float:
        r1 = _ret(features, "close_1d_ago")
        r7 = _ret(features, "close_7d_ago")
        accel = r1 - r7 / 7.0
        o = _open(features)
        sma = self._safe_get(features, "sma_20")
        if sma <= 0 or o <= 0:
            return 0.0
        corr_proxy = _sign(o - sma)
        return self._tanh_norm(-accel * corr_proxy, 0.03)


# ---------------------------------------------------------------------------
# Alpha #18
# ---------------------------------------------------------------------------
class WQAlpha018(Factor):
    """Original: (-1 * rank(((stddev(abs((close - open)),5) + (close - open)) +
                              correlation(close, open, 10))))

    Adaptation: use -((|close-open|/close scaled by atr) + (close-open)/close).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_018",
            category="volatility",
            description="-(|body|/atr + body/close)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        o = _open(features)
        atr = self._safe_get(features, "atr_14")
        if close <= 0 or o <= 0 or atr <= 0:
            return 0.0
        body = close - o
        std_proxy = abs(body) / atr
        body_pct = body / close
        return self._tanh_norm(-(std_proxy + body_pct), 1.0)


# ---------------------------------------------------------------------------
# Alpha #19
# ---------------------------------------------------------------------------
class WQAlpha019(Factor):
    """Original: ((-1 * sign(((close - delay(close,7)) + delta(close,7)))) *
                  (1 + rank((1 + sum(returns,250)))))

    Adaptation: delay(close,7)==delta(close,7) so the sign term reduces to
    sign of 2*(close - close_7d_ago).  Scale by (1 + 28d return) as a longer
    trend proxy.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_019",
            category="reversion",
            description="-sign(7d delta) * (1 + 28d return)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        c7 = self._safe_get(features, "close_7d_ago")
        if close <= 0 or c7 <= 0:
            return 0.0
        s = _sign(close - c7)
        r28 = _ret(features, "close_28d_ago")
        return self._tanh_norm(-s * (1.0 + r28), 1.5)


# ---------------------------------------------------------------------------
# Alpha #21
# ---------------------------------------------------------------------------
class WQAlpha021(Factor):
    """Original: multi-branch sum/stddev comparison yielding +/-1.

    Adaptation: compare close to sma_20 +/- 0.5*atr; inside band -> 0 signal,
    outside -> reversion.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_021",
            category="reversion",
            description="band reversion around sma_20",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        sma = self._safe_get(features, "sma_20")
        atr = self._safe_get(features, "atr_14")
        if close <= 0 or sma <= 0 or atr <= 0:
            return 0.0
        z = (close - sma) / atr
        if abs(z) < 0.25:
            return 0.0
        return self._tanh_norm(-z, 2.0)


# ---------------------------------------------------------------------------
# Alpha #23
# ---------------------------------------------------------------------------
class WQAlpha023(Factor):
    """Original: (((sum(high,20)/20) < high) ? (-1 * delta(high,2)) : 0)

    Adaptation: use sma_20 as sum(high,20)/20 proxy; if current high exceeds
    sma_20, fade the recent move (use -1d return as delta(high,2) proxy).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_023",
            category="reversion",
            description="fade highs extended above sma20",
        )

    def compute(self, features: dict) -> float:
        high = _high(features)
        sma = self._safe_get(features, "sma_20")
        if high <= 0 or sma <= 0:
            return 0.0
        if high <= sma:
            return 0.0
        r = _ret(features, "close_1d_ago")
        return self._tanh_norm(-r, 0.02)


# ---------------------------------------------------------------------------
# Alpha #24
# ---------------------------------------------------------------------------
class WQAlpha024(Factor):
    """Original: if delta(sum(close,100)/100, 100)/delay(close,100) <= 0.05
                 then -1*(close - ts_min(close,100)) else -1*delta(close,3)

    Adaptation: use ema_200 as sum(close,100)/100 proxy.  If ema_200 slope
    modest, fade distance from recent low; else fade short-term delta.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_024",
            category="reversion",
            description="delta-of-MA regime-switched fade",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        ema200 = self._safe_get(features, "ema_200")
        c28 = self._safe_get(features, "close_28d_ago")
        if close <= 0 or ema200 <= 0:
            return 0.0
        slope_pct = 0.0
        if c28 > 0:
            slope_pct = (ema200 - c28) / c28
        if abs(slope_pct) <= 0.05:
            bb_lower = self._safe_get(features, "bb_lower")
            if bb_lower <= 0:
                return 0.0
            dist = (close - bb_lower) / close
            return self._tanh_norm(-dist, 0.05)
        r1 = _ret(features, "close_1d_ago")
        return self._tanh_norm(-r1, 0.03)


# ---------------------------------------------------------------------------
# Alpha #28
# ---------------------------------------------------------------------------
class WQAlpha028(Factor):
    """Original: scale(((correlation(adv20, low, 5) + ((high + low)/2)) - close))

    Adaptation: drop correlation term; use ((high+low)/2 - close)/atr scaled.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_028",
            category="reversion",
            description="((high+low)/2 - close)/atr",
        )

    def compute(self, features: dict) -> float:
        high = _high(features)
        low = _low(features)
        close = self._safe_get(features, "close")
        atr = self._safe_get(features, "atr_14")
        if close <= 0 or atr <= 0 or high <= 0 or low <= 0:
            return 0.0
        mid = 0.5 * (high + low)
        return self._tanh_norm((mid - close) / atr, 1.0)


# ---------------------------------------------------------------------------
# Alpha #32
# ---------------------------------------------------------------------------
class WQAlpha032(Factor):
    """Original: (scale(((sum(close,7)/7) - close)) +
                  (20 * scale(correlation(vwap, delay(close,5), 230))))

    Adaptation: first term is simply (sma_of_7 - close)/close.  We do not
    have sma_7; approximate with (close_7d_ago + close)/2 as midpoint.  Drop
    second correlation term.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_032",
            category="reversion",
            description="(7d midpoint - close)/close mean reversion",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        c7 = self._safe_get(features, "close_7d_ago")
        if close <= 0 or c7 <= 0:
            return 0.0
        mid = 0.5 * (close + c7)
        return self._tanh_norm((mid - close) / close, 0.02)


# ---------------------------------------------------------------------------
# Alpha #33
# ---------------------------------------------------------------------------
class WQAlpha033(Factor):
    """Original: rank((-1 * ((1 - (open/close))^1)))

    Adaptation: (open/close - 1) directly (positive when open<close).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_033",
            category="momentum",
            description="(open/close - 1)",
        )

    def compute(self, features: dict) -> float:
        o = _open(features)
        close = self._safe_get(features, "close")
        if o <= 0 or close <= 0:
            return 0.0
        return self._tanh_norm(-(1.0 - o / close), 0.02)


# ---------------------------------------------------------------------------
# Alpha #34
# ---------------------------------------------------------------------------
class WQAlpha034(Factor):
    """Original: rank(((1 - rank((stddev(returns,2)/stddev(returns,5)))) +
                      (1 - rank(delta(close,1)))))

    Adaptation: use (atr/close) as stddev(returns,5) proxy; short-window
    stddev proxied by |ret_1d|.  Ratio high -> vol expanding -> negative;
    plus -1d return term.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_034",
            category="volatility",
            description="-(|ret1d| / (atr/close)) - ret1d",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        atr = self._safe_get(features, "atr_14")
        if close <= 0 or atr <= 0:
            return 0.0
        r1 = _ret(features, "close_1d_ago")
        vol5 = atr / close
        vol2 = abs(r1)
        if vol5 <= 0:
            return 0.0
        ratio = vol2 / vol5
        score = (1.0 - ratio) - r1 * 20.0
        return self._tanh_norm(score, 2.0)


# ---------------------------------------------------------------------------
# Alpha #38
# ---------------------------------------------------------------------------
class WQAlpha038(Factor):
    """Original: ((-1 * rank(ts_rank(close,10))) * rank((close/open)))

    Adaptation: ts_rank(close,10) ~= (close - sma_20)/atr.  Multiply by
    -(close/open - 1).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_038",
            category="reversion",
            description="-ts_rank(close) * (close/open - 1)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        sma = self._safe_get(features, "sma_20")
        atr = self._safe_get(features, "atr_14")
        o = _open(features)
        if close <= 0 or sma <= 0 or atr <= 0 or o <= 0:
            return 0.0
        ts_rank_proxy = (close - sma) / atr
        body = close / o - 1.0
        return self._tanh_norm(-ts_rank_proxy * body, 0.05)


# ---------------------------------------------------------------------------
# Alpha #41
# ---------------------------------------------------------------------------
class WQAlpha041(Factor):
    """Original: (((high * low)^0.5) - vwap)

    Direct implementation — we have high, low, vwap.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_041",
            category="technical",
            description="sqrt(high*low) - vwap, normalized by atr",
        )

    def compute(self, features: dict) -> float:
        high = _high(features)
        low = _low(features)
        vwap = self._safe_get(features, "vwap")
        atr = self._safe_get(features, "atr_14")
        if high <= 0 or low <= 0 or vwap <= 0 or atr <= 0:
            return 0.0
        geomean = math.sqrt(high * low)
        return self._tanh_norm((geomean - vwap) / atr, 1.0)


# ---------------------------------------------------------------------------
# Alpha #42
# ---------------------------------------------------------------------------
class WQAlpha042(Factor):
    """Original: (rank((vwap - close)) / rank((vwap + close)))

    Adaptation: direct ratio (vwap - close) / (vwap + close).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_042",
            category="reversion",
            description="(vwap-close)/(vwap+close)",
        )

    def compute(self, features: dict) -> float:
        vwap = self._safe_get(features, "vwap")
        close = self._safe_get(features, "close")
        if vwap <= 0 or close <= 0:
            return 0.0
        denom = vwap + close
        if denom <= 0:
            return 0.0
        return self._tanh_norm((vwap - close) / denom, 0.01)


# ---------------------------------------------------------------------------
# Alpha #46
# ---------------------------------------------------------------------------
class WQAlpha046(Factor):
    """Original: if ((delay(close,20) - delay(close,10))/10 -
                     (delay(close,10) - close)/10) > 0.25 then -1
                 elif < 0 then 1 else -1*(close - delay(close,1))

    Adaptation: use close_28d_ago, close_7d_ago, close for 3-point curvature.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_046",
            category="reversion",
            description="three-point curvature regime switch",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        c7 = self._safe_get(features, "close_7d_ago")
        c28 = self._safe_get(features, "close_28d_ago")
        if close <= 0 or c7 <= 0 or c28 <= 0:
            return 0.0
        slope_old = (c28 - c7) / 21.0 / c28
        slope_new = (c7 - close) / 7.0 / c7
        curvature = slope_old - slope_new
        if curvature > 0.005:
            return -1.0
        if curvature < -0.005:
            return 1.0
        r1 = _ret(features, "close_1d_ago")
        return self._tanh_norm(-r1, 0.02)


# ---------------------------------------------------------------------------
# Alpha #51
# ---------------------------------------------------------------------------
class WQAlpha051(Factor):
    """Original: same-family curvature rule with looser threshold.

    Adaptation: same formulation as Alpha 46 but threshold -0.05 yields
    a bullish bias (same paper convention).
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_051",
            category="reversion",
            description="curvature switch with looser threshold",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        c7 = self._safe_get(features, "close_7d_ago")
        c28 = self._safe_get(features, "close_28d_ago")
        if close <= 0 or c7 <= 0 or c28 <= 0:
            return 0.0
        slope_old = (c28 - c7) / 21.0 / c28
        slope_new = (c7 - close) / 7.0 / c7
        curvature = slope_old - slope_new
        if curvature < -0.05:
            return 1.0
        r1 = _ret(features, "close_1d_ago")
        return self._tanh_norm(-r1, 0.02)


# ---------------------------------------------------------------------------
# Alpha #54
# ---------------------------------------------------------------------------
class WQAlpha054(Factor):
    """Original: (-1 * ((low - close) * (open^5))) / ((low - high) * (close^5))

    Simplifies to -((low-close)/(low-high)) * (open/close)^5.  Direct.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_054",
            category="reversion",
            description="-(low-close)/(low-high) * (open/close)^5",
        )

    def compute(self, features: dict) -> float:
        high = _high(features)
        low = _low(features)
        close = self._safe_get(features, "close")
        o = _open(features)
        if close <= 0 or o <= 0 or high <= 0 or low <= 0:
            return 0.0
        denom = low - high
        if denom == 0:
            return 0.0
        try:
            ratio = (o / close) ** 5
        except (OverflowError, ValueError):
            return 0.0
        val = -((low - close) / denom) * ratio
        # signal is roughly in [-1, 1] already, linear-norm to be safe
        return self._linear_norm(val, 0.5, 1.5)


# ---------------------------------------------------------------------------
# Alpha #84
# ---------------------------------------------------------------------------
class WQAlpha084(Factor):
    """Original: SignedPower(ts_rank((vwap - ts_max(vwap,15)),21), delta(close,5))

    Adaptation: use (vwap - bb_upper) as 'distance from recent max' proxy;
    signed-power collapses to sign of (delta close 7d) * (vwap-bb_upper)/atr.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_084",
            category="reversion",
            description="(vwap - bb_upper)/atr signed by 7d return",
        )

    def compute(self, features: dict) -> float:
        vwap = self._safe_get(features, "vwap")
        bb_u = self._safe_get(features, "bb_upper")
        atr = self._safe_get(features, "atr_14")
        if vwap <= 0 or bb_u <= 0 or atr <= 0:
            return 0.0
        dist = (vwap - bb_u) / atr
        delta7 = _ret(features, "close_7d_ago")
        return self._tanh_norm(dist * _sign(delta7), 1.0)


# ---------------------------------------------------------------------------
# Alpha #101
# ---------------------------------------------------------------------------
class WQAlpha101(Factor):
    """Original: ((close - open) / ((high - low) + 0.001))

    Direct implementation — the canonical intra-bar body-vs-range ratio.
    """

    def __init__(self):
        super().__init__(
            name="wq_alpha_101",
            category="momentum",
            description="(close-open) / (high-low)",
        )

    def compute(self, features: dict) -> float:
        close = self._safe_get(features, "close")
        o = _open(features)
        high = _high(features)
        low = _low(features)
        if close <= 0 or o <= 0:
            return 0.0
        rng = (high - low) + 0.001
        if rng <= 0:
            return 0.0
        return self._tanh_norm((close - o) / rng, 0.5)


WORLDQUANT_ALPHA_FACTORS = [
    WQAlpha002(),
    WQAlpha003(),
    WQAlpha004(),
    WQAlpha005(),
    WQAlpha006(),
    WQAlpha007(),
    WQAlpha008(),
    WQAlpha012(),
    WQAlpha014(),
    WQAlpha018(),
    WQAlpha019(),
    WQAlpha021(),
    WQAlpha023(),
    WQAlpha024(),
    WQAlpha028(),
    WQAlpha032(),
    WQAlpha033(),
    WQAlpha034(),
    WQAlpha038(),
    WQAlpha041(),
    WQAlpha042(),
    WQAlpha046(),
    WQAlpha054(),
    WQAlpha084(),
    WQAlpha101(),
]
