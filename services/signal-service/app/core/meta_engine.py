"""Meta-ensemble signal engine (Phases F-J) for live signal-service.

Given an asset, pulls recent OHLCV from market-data, runs the
configured alpha panel, combines them through
`shared.portfolio.meta_ensemble.combine`, and returns a single signal
score + full decomposition for transparency.

This sits next to the classical `build_signal_response()` scoring path
so teams can A/B test. Gated by `SIGNAL_META_ENABLED` — when false the
endpoint returns 503 so frontends fall back to the legacy signal.

Cache:
  - OHLCV per (asset, interval) cached in-memory for *ttl_seconds*.
    At 1h bars a 30-second TTL means ≤1 extra market-data hit per bar
    per concurrent caller.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from shared.alpha.base import AlphaConfig
from shared.alpha.registry import ALPHA_REGISTRY, get_alpha

# IP modules — present only when private quant-alpha is mounted. Public-only
# builds get None placeholders; meta-engine then degrades to the legacy
# scoring path (engine is feature-gated by SIGNAL_META_ENABLED anyway).
try:
    from shared.alpha.ml_refit import MLRefitScheduler
except ImportError:
    MLRefitScheduler = None  # type: ignore

try:
    from shared.portfolio.kelly_store import KellyStore
except ImportError:
    KellyStore = None  # type: ignore

try:
    from shared.portfolio.meta_ensemble import (
        MetaEnsembleConfig,
        combine,
        compute_regime_kelly,
    )
except ImportError:
    MetaEnsembleConfig = None  # type: ignore
    combine = compute_regime_kelly = None  # type: ignore


# Default alpha pool = production-ready subset from the registry audit
# (scripts/alpha_audit.py, 2026-04-15). The meta combiner filters these
# further by rolling Sharpe before weighting, so keeping the pool wide
# is safe — losing alphas get weighted to zero, they don't bleed PnL.
try:
    from shared.alpha.registry import PRODUCTION_READY_ALPHAS

    _DEFAULT_ALPHA_LIST = ",".join(sorted(PRODUCTION_READY_ALPHAS))
except Exception:
    _DEFAULT_ALPHA_LIST = "technical_ensemble,momentum_ensemble,range_reversion,vol_breakout"

_DEFAULT_ALPHAS = os.getenv("SIGNAL_META_ALPHAS", _DEFAULT_ALPHA_LIST).split(",")

_META_ENABLED = os.getenv("SIGNAL_META_ENABLED", "false").lower() == "true"
_MAKER_MODE = os.getenv("SIGNAL_META_MAKER_MODE", "true").lower() == "true"


@dataclass
class _CacheEntry:
    df: pd.DataFrame
    stored_at: float


class MetaSignalEngine:
    def __init__(
        self,
        market_data_client,
        *,
        alphas: list[str] | None = None,
        history_bars: int = 500,
        ttl_seconds: float = 30.0,
        kelly_store: KellyStore | None = None,
        ml_scheduler: MLRefitScheduler | None = None,
    ) -> None:
        self._mdc = market_data_client
        self._alpha_names = [a.strip() for a in (alphas or _DEFAULT_ALPHAS) if a.strip()]
        self._history_bars = history_bars
        # Phase O: long-horizon Kelly blending. Local window is only
        # 500 bars — too thin for stable per-regime edge estimation.
        # Blend with the 8-yr persisted snapshot written by
        # scripts/bootstrap_kelly.py + outcome_consumer.
        self._kelly_store = kelly_store or KellyStore()
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        # ML alpha auto-refit scheduler. Promoted alphas are auto-included
        # in _alpha_panel. Refit itself is driven externally (async) via
        # maybe_refit_ml_alphas() — evaluate() only *reads* promoted_alphas.
        self._ml_scheduler = ml_scheduler or MLRefitScheduler()

    # ------------------------------------------------------------------

    def maybe_refit_ml_alphas(self, df: pd.DataFrame) -> list:
        """Trigger refit+validate for any ML alpha due for retraining.

        Intended for async/out-of-band invocation (cron, background task).
        Do NOT call this inside evaluate() — refit is slow and would block
        the signal response.
        """
        import logging
        _logger = logging.getLogger("meta-signal-engine")

        results = []
        current_bar = len(df)
        for alpha_name in ("ml_forest", "ml_meta"):
            if not self._ml_scheduler.should_refit(alpha_name, current_bar=current_bar):
                continue
            try:
                res = self._ml_scheduler.refit_and_validate(alpha_name, df, current_bar)
                results.append(res)
                _logger.info(
                    "ml_refit_completed",
                    extra={
                        "alpha": alpha_name,
                        "passed": res.passed,
                        "oos_sharpe": res.oos_sharpe,
                        "reason": res.reason,
                    },
                )
            except Exception as exc:
                _logger.warning(
                    "ml_refit_exception",
                    extra={"alpha": alpha_name, "error": str(exc)[:200]},
                )
        return results

    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return _META_ENABLED

    @property
    def alpha_names(self) -> list[str]:
        return list(self._alpha_names)

    # ------------------------------------------------------------------

    def _label_regime(self, df: pd.DataFrame) -> pd.Series:
        from shared.regime_vectorized import classify_regime
        return classify_regime(df)

    def _load_ohlcv(self, asset: str, interval: str = "1h") -> pd.DataFrame:
        key = (asset, interval)
        now = time.monotonic()
        ent = self._cache.get(key)
        if ent and (now - ent.stored_at) < self._ttl:
            return ent.df
        df = self._mdc.get_history(asset, limit=self._history_bars, interval=interval)
        if not df.empty:
            self._cache[key] = _CacheEntry(df=df, stored_at=now)
        return df

    def _alpha_panel(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run each configured alpha, collecting their position series.

        Includes any ML alpha that the MLRefitScheduler has marked as
        promoted (i.e. passed walk-forward gates on its last refit).
        """
        columns: dict[str, pd.Series] = {}
        import logging
        _logger = logging.getLogger("meta-signal-engine")

        # Merge default alpha list with any ML alphas promoted by the refit
        # scheduler. A promoted ML alpha is one whose last walk-forward
        # validation cleared the RefitGates; it's safe to include live.
        live_alphas = list(self._alpha_names)
        for ml_name in self._ml_scheduler.promoted_alphas:
            if ml_name not in live_alphas:
                live_alphas.append(ml_name)
                _logger.info(
                    "ml_alpha_included",
                    extra={"alpha": ml_name, "source": "ml_refit_scheduler"},
                )

        # Log any ML alpha that is NOT promoted so the exclusion is visible.
        for ml_name in ("ml_forest", "ml_meta"):
            if ml_name not in self._ml_scheduler.promoted_alphas and ml_name not in self._alpha_names:
                _logger.debug(
                    "ml_alpha_excluded",
                    extra={"alpha": ml_name, "reason": "not_promoted"},
                )

        for name in live_alphas:
            if name not in ALPHA_REGISTRY:
                _logger.warning("alpha_not_in_registry", extra={"alpha": name})
                continue
            try:
                alpha = get_alpha(name, AlphaConfig(name=name, asset_type="crypto"))
                signal = alpha.generate(df)
                columns[name] = signal.position.reindex(df.index).fillna(0.0)
            except Exception as exc:
                _logger.warning(
                    "alpha_panel_exception",
                    extra={
                        "alpha": name,
                        "error": str(exc)[:200],
                        "bars": len(df),
                    },
                )
                continue
        return pd.DataFrame(columns)

    # ------------------------------------------------------------------

    def evaluate(
        self,
        asset: str,
        *,
        interval: str = "1h",
        threshold: float = 0.6,
        config: MetaEnsembleConfig | None = None,
    ) -> dict:
        df = self._load_ohlcv(asset, interval)
        if df.empty or len(df) < 200:
            return {
                "asset": asset,
                "status": "insufficient_data",
                "bars": len(df),
                "signal_score": 0.0,
                "direction": "HOLD",
            }

        positions = self._alpha_panel(df)
        if positions.empty:
            return {
                "asset": asset,
                "status": "no_alpha_signals",
                "signal_score": 0.0,
                "direction": "HOLD",
            }

        bar_ret = df["close"].pct_change().fillna(0.0)
        regime = self._label_regime(df)
        cfg = config or MetaEnsembleConfig()
        result = combine(
            positions,
            bar_ret,
            regime=regime,
            config=cfg,
        )

        # Blend local Kelly with persisted long-horizon snapshot.
        local_kelly = result.get("kelly_table", {})
        try:
            combined_pnl_series = result["raw_combined"] * bar_ret
            local_table = compute_regime_kelly(
                combined_pnl_series,
                regime.reindex(combined_pnl_series.index).fillna("unknown"),
                min_samples=cfg.kelly_min_samples,
                half_kelly=cfg.kelly_half,
                kelly_cap=cfg.kelly_cap,
            )
            blended_kelly = self._kelly_store.blend(
                local_table.fractions,
                local_table.samples,
                persist=False,
            )
        except Exception:
            blended_kelly = dict(local_kelly)

        latest_score = float(result["position"].iloc[-1])
        latest_regime = str(regime.iloc[-1])
        latest_dd_mult = float(result["dd_multiplier"].iloc[-1])
        # Blended Kelly override: combine() already applied local Kelly
        # to the position. If the long-horizon blended fraction differs,
        # we replace the local fraction rather than multiplying on top
        # (which would cause double-Kelly sizing).
        if blended_kelly and latest_regime in blended_kelly:
            local_k = local_kelly.get(latest_regime)
            blended_k = blended_kelly[latest_regime]
            if local_k and local_k > 0 and abs(blended_k - local_k) > 0.02:
                # Replace local Kelly with blended: undo local, apply blended
                # position_without_kelly = latest_score / local_k
                # new_position = position_without_kelly * blended_k
                # Simplified: latest_score * (blended_k / local_k), capped
                adjustment = min(max(blended_k / local_k, 0.5), 1.5)
                latest_score *= adjustment
        direction = (
            "BUY"
            if latest_score >= threshold
            else "SELL" if latest_score <= -threshold else "HOLD"
        )

        # Per-alpha attribution over the last N bars for observability.
        # Exposes which alphas are actually contributing PnL so operators
        # can spot dead alphas (zero weight) or concentrated risk (one
        # alpha dominating). Computed from the panel combine() already
        # produced — no extra simulation cost.
        attribution: dict[str, dict[str, float]] = {}
        pnl_panel = result.get("alpha_pnl_panel")
        weights = result.get("alpha_weights", {})
        if pnl_panel is not None and not pnl_panel.empty:
            # Last 168 bars (1 week at 1h) attribution
            recent = pnl_panel.iloc[-168:] if len(pnl_panel) >= 168 else pnl_panel
            for alpha_name in recent.columns:
                w = weights.get(alpha_name, 0.0)
                contribution = float(recent[alpha_name].sum() * w)
                attribution[alpha_name] = {
                    "weight": round(w, 4),
                    "pnl_contribution_7d": round(contribution, 5),
                    "standalone_sharpe_7d": round(
                        float(recent[alpha_name].mean() / recent[alpha_name].std() * (24 * 365) ** 0.5)
                        if recent[alpha_name].std() > 1e-12 else 0.0,
                        3,
                    ),
                }

        return {
            "asset": asset,
            "status": "ok",
            "signal_score": round(latest_score, 4),
            "direction": direction,
            "threshold": threshold,
            "threshold_crossed": abs(latest_score) >= threshold,
            "regime": latest_regime,
            "dd_multiplier": round(latest_dd_mult, 3),
            "alpha_weights": {k: round(v, 4) for k, v in result["alpha_weights"].items() if v > 1e-4},
            "kelly_table": {k: round(v, 4) for k, v in result["kelly_table"].items()},
            "kelly_blended": {k: round(v, 4) for k, v in blended_kelly.items()},
            "bars_used": len(df),
            "reference_price": float(df["close"].iloc[-1]),
            "feature_timestamp": df.index[-1].isoformat(),
            "maker_mode": _MAKER_MODE,
            "preferred_order_type": "LIMIT" if _MAKER_MODE else "MARKET",
            "target_position": round(latest_score, 4),
            "cvar_multiplier": round(float(result["cvar_multiplier"].iloc[-1]), 4) if "cvar_multiplier" in result else 1.0,
            "ml_promoted_alphas": list(self._ml_scheduler.promoted_alphas),
            "attribution": attribution,
        }
