"""Per-asset LiveDriftMonitor registry for signal-service.

Each asset has its own monitor seeded with the backtest-expected Sharpe
and per-bar volatility. Order-service (or a batch job) feeds realized
trade/bar returns via the `/signals/meta/drift/{asset}/observe` endpoint.

The monitor's `evaluate()` produces a DriftAlert that can page oncall
via the Prometheus gauge or be queried ad-hoc.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from shared.observability.live_drift import DriftAlert, LiveDriftMonitor

logger = logging.getLogger("drift-registry")

STRATEGY_REGISTRY_URL = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")

_HARDCODED_FALLBACK_DEFAULTS: dict[str, dict[str, float]] = {
    "ETHUSDT": {"sharpe": 1.53, "vol": 0.0070},
    "BTCUSDT": {"sharpe": 1.18, "vol": 0.0050},
    "SOLUSDT": {"sharpe": 0.78, "vol": 0.0095},
}


def load_baselines_from_registry() -> dict[str, dict[str, float]]:
    """Fetch baselines from strategy-registry; fall back to defaults on failure."""
    try:
        import httpx
        resp = httpx.get(f"{STRATEGY_REGISTRY_URL}/strategies/baselines", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data:
                logger.info("drift_baselines_loaded_from_registry", extra={"count": len(data)})
                return data
    except Exception as exc:
        logger.debug("drift_baselines_registry_unavailable", extra={"error": str(exc)[:100]})
    return _HARDCODED_FALLBACK_DEFAULTS


def _load_baselines() -> dict[str, dict[str, float]]:
    """Load backtest baselines from registry + env overrides, falling back to
    hard-coded defaults.

    Env format: DRIFT_BASELINE_ETHUSDT_SHARPE=1.53, DRIFT_BASELINE_ETHUSDT_VOL=0.007
    Assets discovered from DRIFT_BASELINE_ASSETS=ETHUSDT,BTCUSDT,SOLUSDT (csv).
    """
    defaults = load_baselines_from_registry()
    asset_csv = os.getenv("DRIFT_BASELINE_ASSETS")
    if asset_csv:
        assets = [a.strip() for a in asset_csv.split(",") if a.strip()]
    else:
        assets = list(defaults.keys())

    out: dict[str, dict[str, float]] = {}
    for a in assets:
        d = defaults.get(a, {"sharpe": 0.0, "vol": 0.01})
        out[a] = {
            "sharpe": float(os.getenv(f"DRIFT_BASELINE_{a}_SHARPE", d["sharpe"])),
            "vol": float(os.getenv(f"DRIFT_BASELINE_{a}_VOL", d["vol"])),
        }
    return out


_BACKTEST_BASELINES = _load_baselines()


_REDIS_PREFIX = "drift:obs:"
_PERSIST_EVERY = int(os.getenv("DRIFT_PERSIST_EVERY", "10"))


def _get_redis():
    url = os.getenv("REDIS_URL", "")
    if not url:
        return None
    try:
        from shared.persistence import RedisStore
        return RedisStore(url)
    except Exception:
        return None


class _Registry:
    def __init__(self) -> None:
        self._monitors: dict[str, LiveDriftMonitor] = {}
        self._lock = threading.Lock()
        self._obs_count: dict[str, int] = {}
        self._redis = _get_redis()
        self._window = int(os.getenv("DRIFT_WINDOW_BARS", "500"))
        self._warn_z = float(os.getenv("DRIFT_WARN_Z", "1.5"))
        self._breach_z = float(os.getenv("DRIFT_BREACH_Z", "2.5"))

    def get(self, asset: str) -> LiveDriftMonitor:
        with self._lock:
            mon = self._monitors.get(asset)
            if mon is not None:
                return mon
            baseline = _BACKTEST_BASELINES.get(asset)
            if baseline is None:
                # Unknown asset: create monitor with neutral baseline so the
                # endpoint doesn't 404. The caller can seed a real baseline
                # via observations, though the z-score is then uninformative.
                baseline = {"sharpe": 0.0, "vol": 0.01}
            mon = LiveDriftMonitor(
                strategy_id=f"meta:{asset}",
                backtest_sharpe=baseline["sharpe"],
                backtest_volatility=baseline["vol"],
                window_bars=self._window,
                warn_z=self._warn_z,
                breach_z=self._breach_z,
            )
            self._restore(asset, mon)
            self._monitors[asset] = mon
            return mon

    def _restore(self, asset: str, mon: LiveDriftMonitor) -> None:
        if self._redis is None:
            return
        try:
            raw = self._redis.get(f"{_REDIS_PREFIX}{asset}")
            if raw:
                obs = json.loads(raw) if isinstance(raw, str) else raw
                for r in obs:
                    mon.observe(float(r))
        except Exception:
            pass

    def _persist(self, asset: str) -> None:
        if self._redis is None:
            return
        mon = self._monitors.get(asset)
        if mon is None:
            return
        try:
            self._redis.set(
                f"{_REDIS_PREFIX}{asset}",
                json.dumps(list(mon._returns)),
            )
        except Exception:
            pass

    def observe(self, asset: str, trade_return: float) -> None:
        self.get(asset).observe(trade_return)
        cnt = self._obs_count.get(asset, 0) + 1
        self._obs_count[asset] = cnt
        if cnt % _PERSIST_EVERY == 0:
            self._persist(asset)

    def evaluate(self, asset: str) -> DriftAlert:
        alert = self.get(asset).evaluate()
        # Auto kill-switch: on breach, set Redis kill flag so compliance
        # gateway blocks new live orders. Ops can manually reset via
        # `redis-cli SET kill_switch:global false`.
        if alert.level == "breach" and self._redis is not None:
            try:
                self._redis.set("kill_switch:global", "true")
            except Exception:
                pass
        return alert

    def known_assets(self) -> list[str]:
        return list(_BACKTEST_BASELINES.keys())


registry = _Registry()
