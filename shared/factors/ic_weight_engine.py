"""IC-based dynamic factor weighting engine.

Replaces heuristic regime-based weights with data-driven Information Coefficient
weights. Each factor's weight is proportional to its rolling IC (Spearman rank
correlation with forward returns), decayed by IC instability (IC_IR).

References:
  - Grinold & Kahn (2000) "Active Portfolio Management" — IC as alpha proxy
  - Lopez de Prado (2018) "Advances in Financial Machine Learning" ch. 6
  - Kakushadze (2016) "101 Formulaic Alphas" — factor orthogonality
  - Qian, Hua, Sorensen (2007) "Quantitative Equity Portfolio Management"

Design:
  - Rolling window IC computed over last N decisions (default 200)
  - IC_IR = mean(IC) / std(IC) — measures signal stability
  - Weight = |IC| * sign_adjustment * stability_penalty
  - Negative IC factors get inverted (weight flipped), not removed
  - Factors with IC < noise_threshold get zero weight
  - Weights stored in Redis, refreshed by learning scheduler
  - Falls back to equal weights if insufficient data
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field

from prometheus_client import Counter, Gauge

logger = logging.getLogger("ic-weight-engine")

# ──────────────────────────────────────────────────────────────────
# Prometheus metrics — exported by any service importing this module.
# Labels kept shallow (factor + regime) to stay under Prometheus
# cardinality budgets.
# ──────────────────────────────────────────────────────────────────
ALPHA_IC = Gauge(
    "alpha_ic",
    "Rolling Spearman IC between factor score and forward return",
    ["factor", "regime"],
)
ALPHA_IC_IR = Gauge(
    "alpha_ic_ir",
    "IC information ratio — mean(sub-IC)/std(sub-IC), higher = more stable",
    ["factor", "regime"],
)
ALPHA_WEIGHT = Gauge(
    "alpha_weight",
    "Current ensemble weight assigned to factor after IC + stability scoring",
    ["factor", "regime"],
)
ALPHA_OBS = Gauge(
    "alpha_observations",
    "Rolling observation count backing the current IC estimate",
    ["factor", "regime"],
)
ALPHA_INVERTED = Gauge(
    "alpha_inverted",
    "1 if factor is sign-flipped (negative IC), 0 otherwise",
    ["factor", "regime"],
)
ALPHA_DRIFT_EVENTS = Counter(
    "alpha_drift_events_total",
    "Times a factor's IC dropped by more than drift_threshold vs its baseline",
    ["factor", "regime", "direction"],
)
ALPHA_RECOMPUTE_LATENCY = Gauge(
    "alpha_recompute_latency_seconds",
    "Wall-clock cost of the last recompute_weights() call",
)

# ──────────────────────────────────────────────────────────────────
# Constants from quantitative research
# ──────────────────────────────────────────────────────────────────
MIN_OBSERVATIONS = 50          # Minimum decisions before IC is meaningful
ROLLING_WINDOW = 200           # IC computed over last N decisions
IC_SUBWINDOW = 50              # Sub-window for IC_IR calculation
NOISE_THRESHOLD = 0.015        # |IC| below this → zero weight
STABILITY_PENALTY_BELOW = 0.3  # IC_IR below this → weight penalty
MAX_SINGLE_WEIGHT = 0.35       # No single factor > 35% of total
MIN_ACTIVE_FACTORS = 3         # Fall back to equal if fewer survive

REDIS_IC_KEY = "learning:ic_weights:v2"
REDIS_IC_STATE_KEY = "learning:ic_state:v2"


@dataclass
class FactorICState:
    """Rolling IC state for a single factor in a single regime.

    A factor's predictive power varies with regime (trending vs ranging,
    high-vol vs low-vol, risk-on vs risk-off). We keep one state per
    (factor, regime) pair; the "all" pseudo-regime aggregates across all
    observations for backwards compatibility with callers that don't pass
    regime labels.
    """
    name: str
    regime: str = "all"
    scores: list[float] = field(default_factory=list)
    forward_returns: list[float] = field(default_factory=list)
    ic: float = 0.0
    ic_ir: float = 0.0
    weight: float = 0.0
    n_obs: int = 0
    inverted: bool = False
    # Drift tracking: EMA of IC used as baseline, last-seen IC, peak IC.
    # When |ic - baseline| / max(|baseline|, 0.01) exceeds DRIFT_THRESHOLD
    # we fire a Counter event and a WARNING log line.
    ic_baseline: float = 0.0
    ic_peak: float = 0.0
    last_drift_ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "regime": self.regime,
            "ic": round(self.ic, 4),
            "ic_ir": round(self.ic_ir, 3),
            "weight": round(self.weight, 4),
            "n_obs": self.n_obs,
            "inverted": self.inverted,
            "ic_baseline": round(self.ic_baseline, 4),
            "ic_peak": round(self.ic_peak, 4),
        }


def _spearman_rank_corr(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation. Pure python."""
    n = len(xs)
    if n < 5:
        return 0.0

    def rank(values):
        sorted_idx = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(values):
            j = i
            while j + 1 < len(values) and values[sorted_idx[j + 1]] == values[sorted_idx[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[sorted_idx[k]] = avg_rank
            i = j + 1
        return ranks

    rx = rank(xs)
    ry = rank(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


class ICWeightEngine:
    """Maintains rolling IC state and computes data-driven factor weights."""

    # Drift detector — fires a Counter + log when IC moves by this
    # fraction relative to its EMA baseline. Value tuned so ~5% of normal
    # market transitions don't trigger, but genuine regime breaks do.
    DRIFT_THRESHOLD = 0.35
    DRIFT_COOLDOWN_SECONDS = 3600  # no repeat alerts within 1h per factor/regime
    BASELINE_ALPHA = 0.05  # EMA smoothing for ic_baseline

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._states: dict[str, FactorICState] = {}
        self._weights: dict[str, float] = {}
        # Regime-conditional shadow state — observability-only in Phase A.
        # Phase B consumes this to swap weights based on the current regime.
        # Key: (factor_name, regime_label). Kept separate from self._states
        # so the production weighting path is unchanged and stays
        # behavior-identical to the pre-Phase-A codebase.
        self._regime_states: dict[tuple[str, str], FactorICState] = {}
        self._loaded = False
        self._lock = threading.Lock()  # guards all mutable state

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis
            self._redis = redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://redis:6379/0"),
                decode_responses=True,
            )
            return self._redis
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────
    # Core: update with new observation
    # ──────────────────────────────────────────────────────────────

    def update(
        self,
        factor_scores: dict[str, float],
        forward_return: float,
        regime: str | None = None,
    ) -> None:
        """Add a new observation (decision components + realized forward return).

        Call this from the learning scheduler when hindsight data is available.
        Thread-safe: guarded by self._lock.

        Passing *regime* routes the observation to both the aggregate
        pool ("all") and a regime-specific pool. Regime-conditional pools
        drive observability in Phase A and adaptive weighting in Phase B;
        the aggregate pool still drives the production weights.
        """
        with self._lock:
            for fname, score in factor_scores.items():
                if not isinstance(score, (int, float)) or not math.isfinite(score):
                    continue
                if fname not in self._states:
                    self._states[fname] = FactorICState(name=fname)

                state = self._states[fname]
                state.scores.append(score)
                state.forward_returns.append(forward_return)

                # Keep rolling window
                if len(state.scores) > ROLLING_WINDOW:
                    state.scores = state.scores[-ROLLING_WINDOW:]
                    state.forward_returns = state.forward_returns[-ROLLING_WINDOW:]

                state.n_obs = len(state.scores)

                if regime:
                    key = (fname, regime)
                    rstate = self._regime_states.get(key)
                    if rstate is None:
                        rstate = FactorICState(name=fname, regime=regime)
                        self._regime_states[key] = rstate
                    rstate.scores.append(score)
                    rstate.forward_returns.append(forward_return)
                    if len(rstate.scores) > ROLLING_WINDOW:
                        rstate.scores = rstate.scores[-ROLLING_WINDOW:]
                        rstate.forward_returns = rstate.forward_returns[-ROLLING_WINDOW:]
                    rstate.n_obs = len(rstate.scores)

    def recompute_weights(self) -> dict[str, float]:
        """Recompute all factor weights from current IC state.

        Returns {factor_name: weight} where weights sum to 1.0.
        Thread-safe: guarded by self._lock.
        """
        with self._lock:
            return self._recompute_weights_locked()

    @staticmethod
    def _has_sufficient_diversity(state: FactorICState) -> bool:
        """Return True iff the rolling window has enough distinct scores
        AND distinct forward returns to make Spearman rank correlation
        meaningful.

        V14 restoration: commit 51b199a's message promised this guard in
        ic_weight_engine.py but the diff only landed the scheduler dedupe
        — the engine-side check that the test expects (and that
        production needed; see 2026-05-04 stuck-factor incident) was
        never actually committed. Restoring it now closes that gap.

        Threshold: ≥10% of n_obs unique values on each axis, with an
        absolute floor of 5. Anything below means the rank correlation
        will collapse to ±1.0 from the few non-tied pairs and isn't a
        real signal.
        """
        n = state.n_obs
        if n <= 0:
            return False
        floor = max(5, n // 10)
        return (
            len(set(state.scores)) >= floor
            and len(set(state.forward_returns)) >= floor
        )

    def _recompute_state_ic(self, state: FactorICState) -> None:
        """Populate ic / ic_ir / inverted fields for one state in-place.

        Extracted so the regime shadow pool can reuse identical logic
        without duplicating the core stats. Callers still drive weight
        assignment separately — this only computes observable stats.
        """
        if state.n_obs < MIN_OBSERVATIONS:
            state.ic = 0.0
            state.ic_ir = 0.0
            state.weight = 0.0
            return

        # V14: stuck-factor guard. See _has_sufficient_diversity docstring.
        if not self._has_sufficient_diversity(state):
            state.ic = 0.0
            state.ic_ir = 0.0
            state.weight = 0.0
            state.inverted = False
            return

        state.ic = _spearman_rank_corr(state.scores, state.forward_returns)

        sub_ics: list[float] = []
        n = len(state.scores)
        for start in range(0, n - IC_SUBWINDOW + 1, IC_SUBWINDOW):
            end = start + IC_SUBWINDOW
            sub_ics.append(
                _spearman_rank_corr(state.scores[start:end], state.forward_returns[start:end])
            )

        if len(sub_ics) >= 2:
            ic_mean = sum(sub_ics) / len(sub_ics)
            ic_std = math.sqrt(
                sum((x - ic_mean) ** 2 for x in sub_ics) / (len(sub_ics) - 1)
            )
            state.ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
        else:
            state.ic_ir = 0.0

        state.inverted = state.ic < -NOISE_THRESHOLD

    def _check_drift(self, state: FactorICState) -> None:
        """Update baseline EMA and emit drift event if current IC diverged."""
        import time
        now = time.time()
        baseline = state.ic_baseline
        if abs(baseline) < 1e-9:
            # First observation — seed baseline; no drift check.
            state.ic_baseline = state.ic
            state.ic_peak = state.ic
            return

        ref = max(abs(baseline), 0.01)
        delta = (state.ic - baseline) / ref
        if abs(state.ic) > abs(state.ic_peak):
            state.ic_peak = state.ic

        cooled_down = (now - state.last_drift_ts) > self.DRIFT_COOLDOWN_SECONDS
        if cooled_down and abs(delta) >= self.DRIFT_THRESHOLD:
            direction = "down" if delta < 0 else "up"
            ALPHA_DRIFT_EVENTS.labels(
                factor=state.name, regime=state.regime, direction=direction
            ).inc()
            logger.warning(
                "alpha_drift_detected",
                extra={
                    "factor": state.name,
                    "regime": state.regime,
                    "ic": round(state.ic, 4),
                    "baseline": round(baseline, 4),
                    "delta_pct": round(delta * 100, 1),
                    "peak": round(state.ic_peak, 4),
                    "n_obs": state.n_obs,
                },
            )
            state.last_drift_ts = now

        # Update EMA baseline *after* drift check so large moves don't
        # instantly get absorbed into the baseline and mask themselves.
        state.ic_baseline = (
            self.BASELINE_ALPHA * state.ic + (1 - self.BASELINE_ALPHA) * baseline
        )

    def _emit_metrics(self, state: FactorICState) -> None:
        labels = {"factor": state.name, "regime": state.regime}
        ALPHA_IC.labels(**labels).set(state.ic)
        ALPHA_IC_IR.labels(**labels).set(state.ic_ir)
        ALPHA_WEIGHT.labels(**labels).set(state.weight)
        ALPHA_OBS.labels(**labels).set(state.n_obs)
        ALPHA_INVERTED.labels(**labels).set(1.0 if state.inverted else 0.0)

    def _recompute_weights_locked(self) -> dict[str, float]:
        import time
        _t0 = time.perf_counter()
        for state in self._states.values():
            self._recompute_state_ic(state)
            self._check_drift(state)

        # Regime shadow: stats only, no weight assignment here. Weights
        # stay attached to the aggregate pool in Phase A.
        for rstate in self._regime_states.values():
            self._recompute_state_ic(rstate)
            self._check_drift(rstate)
            self._emit_metrics(rstate)

        # Compute raw weights: |IC| * stability_adjustment
        raw_weights = {}
        for state in self._states.values():
            abs_ic = abs(state.ic)
            if abs_ic < NOISE_THRESHOLD:
                raw_weights[state.name] = 0.0
                continue

            # Stability penalty: reduce weight for unstable factors
            stability = 1.0
            if abs(state.ic_ir) < STABILITY_PENALTY_BELOW:
                stability = max(0.3, abs(state.ic_ir) / STABILITY_PENALTY_BELOW)

            raw_weights[state.name] = abs_ic * stability

        # Normalize to sum = 1.0 with max cap
        active = {k: v for k, v in raw_weights.items() if v > 0}

        if len(active) < MIN_ACTIVE_FACTORS:
            # Fall back to equal weights across all factors with enough data.
            # V14: also enforce diversity in the fallback path — without
            # this, a single stuck factor would still get weight=1.0 in
            # fallback mode (the test_stuck_factor_pathology_zeroed
            # assertion that weight == 0.0 covers this).
            eligible = [
                s.name for s in self._states.values()
                if s.n_obs >= MIN_OBSERVATIONS and self._has_sufficient_diversity(s)
            ]
            if eligible:
                eq_w = 1.0 / len(eligible)
                self._weights = {f: eq_w for f in eligible}
            else:
                self._weights = {}
            for state in self._states.values():
                state.weight = self._weights.get(state.name, 0.0)
                self._emit_metrics(state)
            ALPHA_RECOMPUTE_LATENCY.set(time.perf_counter() - _t0)
            return dict(self._weights)

        # Iterative cap enforcement
        total = sum(active.values())
        weights = {k: v / total for k, v in active.items()}

        for _ in range(5):  # converge cap
            capped = {k: min(v, MAX_SINGLE_WEIGHT) for k, v in weights.items()}
            t = sum(capped.values())
            if t > 0:
                weights = {k: v / t for k, v in capped.items()}
            else:
                break
            if all(v <= MAX_SINGLE_WEIGHT + 0.001 for v in weights.values()):
                break

        self._weights = weights
        for state in self._states.values():
            state.weight = self._weights.get(state.name, 0.0)
            self._emit_metrics(state)

        self._save_to_redis()
        ALPHA_RECOMPUTE_LATENCY.set(time.perf_counter() - _t0)
        return dict(self._weights)

    # ──────────────────────────────────────────────────────────────
    # Read weights (used by signal-service at scoring time)
    # ──────────────────────────────────────────────────────────────

    def get_weights(self) -> dict[str, float]:
        """Get current IC-derived weights. Loads from Redis if not yet loaded."""
        if not self._loaded:
            self._load_from_redis()
            self._loaded = True
        return dict(self._weights)

    def get_factor_state(self, name: str) -> FactorICState | None:
        return self._states.get(name)

    def get_all_states(self) -> dict[str, dict]:
        return {name: state.to_dict() for name, state in self._states.items()}

    def get_regime_states(self) -> dict[str, dict]:
        """Per (factor, regime) state for observability. Flat dict keyed by
        "factor@regime" for easy JSON / Prometheus consumption."""
        return {
            f"{state.name}@{state.regime}": state.to_dict()
            for state in self._regime_states.values()
        }

    def get_regime_weights(
        self,
        regime: str,
        *,
        min_obs: int = MIN_OBSERVATIONS,
    ) -> dict[str, float] | None:
        """Return regime-specific weights if the regime pool has enough data,
        else None. Weight formula identical to the aggregate path so the
        adaptive mode stays on the same calibration curve as production.

        A regime pool is "ready" when at least *min_obs* observations per
        factor and at least MIN_ACTIVE_FACTORS factors meet that bar. Below
        that we return None so the caller falls back to aggregate weights,
        which preserves the backtested Sharpe 1.35-1.54 behavior.
        """
        with self._lock:
            factors = [s for s in self._regime_states.values() if s.regime == regime]
            if not factors:
                return None
            # Only consider factors with enough observations under *this* regime.
            eligible = [s for s in factors if s.n_obs >= min_obs]
            if len(eligible) < MIN_ACTIVE_FACTORS:
                return None

            # Raw weights: |IC| × stability (same shape as aggregate path).
            raw: dict[str, float] = {}
            for s in eligible:
                abs_ic = abs(s.ic)
                if abs_ic < NOISE_THRESHOLD:
                    raw[s.name] = 0.0
                    continue
                stability = 1.0
                if abs(s.ic_ir) < STABILITY_PENALTY_BELOW:
                    stability = max(0.3, abs(s.ic_ir) / STABILITY_PENALTY_BELOW)
                raw[s.name] = abs_ic * stability

            active = {k: v for k, v in raw.items() if v > 0}
            if len(active) < MIN_ACTIVE_FACTORS:
                return None

            total = sum(active.values())
            weights = {k: v / total for k, v in active.items()}
            for _ in range(5):
                capped = {k: min(v, MAX_SINGLE_WEIGHT) for k, v in weights.items()}
                t = sum(capped.values())
                if t <= 0:
                    break
                weights = {k: v / t for k, v in capped.items()}
                if all(v <= MAX_SINGLE_WEIGHT + 0.001 for v in weights.values()):
                    break
            return weights

    def get_regime_inverted(self, regime: str) -> dict[str, bool]:
        """Which factors should be sign-flipped when scoring under *regime*."""
        with self._lock:
            return {
                s.name: s.inverted
                for s in self._regime_states.values()
                if s.regime == regime
            }

    def get_regime_summary(self) -> dict[str, dict]:
        """Pivoted view: {regime: {factor: {ic, ic_ir, n_obs, inverted}}}.

        Intended for the /alpha/catalog endpoint and UI panels.
        """
        summary: dict[str, dict] = {}
        for state in self._regime_states.values():
            summary.setdefault(state.regime, {})[state.name] = {
                "ic": round(state.ic, 4),
                "ic_ir": round(state.ic_ir, 3),
                "n_obs": state.n_obs,
                "inverted": state.inverted,
                "ic_baseline": round(state.ic_baseline, 4),
                "ic_peak": round(state.ic_peak, 4),
            }
        return summary

    def is_inverted(self, factor_name: str) -> bool:
        state = self._states.get(factor_name)
        return state.inverted if state else False

    # ──────────────────────────────────────────────────────────────
    # Redis persistence
    # ──────────────────────────────────────────────────────────────

    def _save_to_redis(self) -> None:
        r = self._get_redis()
        if not r:
            return
        try:
            state_data = {name: state.to_dict() for name, state in self._states.items()}
            # Regime shadow pool — flat dict keyed "factor@regime" so the
            # round-trip stays JSON-safe (tuple keys are not serializable).
            regime_data = {
                f"{s.name}@{s.regime}": {
                    **s.to_dict(),
                    "scores": s.scores[-ROLLING_WINDOW:],
                    "forward_returns": s.forward_returns[-ROLLING_WINDOW:],
                }
                for s in self._regime_states.values()
            }
            pipe = r.pipeline()
            pipe.set(REDIS_IC_KEY, json.dumps(self._weights))
            pipe.set(REDIS_IC_STATE_KEY, json.dumps(state_data))
            pipe.set(f"{REDIS_IC_STATE_KEY}:regime", json.dumps(regime_data))
            pipe.execute()
            logger.info(
                "ic_weights_saved",
                extra={"n_factors": len(self._weights), "n_regimes": len(regime_data)},
            )
        except Exception as e:
            logger.warning(f"ic_weights_save_failed: {e}")

    def _load_from_redis(self) -> None:
        r = self._get_redis()
        if not r:
            return
        try:
            raw = r.get(REDIS_IC_KEY)
            if raw:
                self._weights = json.loads(raw)
            state_raw = r.get(REDIS_IC_STATE_KEY)
            if state_raw:
                for name, data in json.loads(state_raw).items():
                    if name not in self._states:
                        self._states[name] = FactorICState(
                            name=name,
                            ic=data.get("ic", 0),
                            ic_ir=data.get("ic_ir", 0),
                            weight=data.get("weight", 0),
                            n_obs=data.get("n_obs", 0),
                            inverted=data.get("inverted", False),
                        )
            # Regime shadow pool: restore full rolling arrays if present.
            regime_raw = r.get(f"{REDIS_IC_STATE_KEY}:regime")
            if regime_raw:
                for key, data in json.loads(regime_raw).items():
                    if "@" not in key:
                        continue
                    name, regime = key.split("@", 1)
                    state_key = (name, regime)
                    if state_key in self._regime_states:
                        continue
                    rstate = FactorICState(
                        name=name,
                        regime=regime,
                        scores=data.get("scores", []),
                        forward_returns=data.get("forward_returns", []),
                        ic=data.get("ic", 0.0),
                        ic_ir=data.get("ic_ir", 0.0),
                        weight=data.get("weight", 0.0),
                        n_obs=data.get("n_obs", 0),
                        inverted=data.get("inverted", False),
                        ic_baseline=data.get("ic_baseline", 0.0),
                        ic_peak=data.get("ic_peak", 0.0),
                    )
                    self._regime_states[state_key] = rstate
            # Also restore rolling data from snapshot if available,
            # so recompute_weights() has actual scores to work with
            self.load_state_snapshot()
        except Exception as e:
            logger.debug(f"ic_weights_load_failed: {e}")

    def save_state_snapshot(self) -> None:
        """Persist full rolling data for recovery (larger payload)."""
        r = self._get_redis()
        if not r:
            return
        try:
            snapshot = {}
            for name, state in self._states.items():
                snapshot[name] = {
                    "scores": state.scores[-ROLLING_WINDOW:],
                    "forward_returns": state.forward_returns[-ROLLING_WINDOW:],
                }
            r.set(f"{REDIS_IC_STATE_KEY}:snapshot", json.dumps(snapshot))
        except Exception as e:
            logger.warning(f"ic_snapshot_save_failed: {e}")

    def load_state_snapshot(self) -> bool:
        """Restore rolling data from snapshot."""
        r = self._get_redis()
        if not r:
            return False
        try:
            raw = r.get(f"{REDIS_IC_STATE_KEY}:snapshot")
            if not raw:
                return False
            snapshot = json.loads(raw)
            for name, data in snapshot.items():
                if name not in self._states:
                    self._states[name] = FactorICState(name=name)
                self._states[name].scores = data.get("scores", [])
                self._states[name].forward_returns = data.get("forward_returns", [])
                self._states[name].n_obs = len(self._states[name].scores)
            return True
        except Exception as e:
            logger.debug(f"ic_snapshot_load_failed: {e}")
            return False


# ──────────────────────────────────────────────────────────────────
# Module-level singleton (used by signal-service and learning scheduler)
# ──────────────────────────────────────────────────────────────────
_engine: ICWeightEngine | None = None


def get_ic_engine() -> ICWeightEngine:
    global _engine
    if _engine is None:
        _engine = ICWeightEngine()
    return _engine
