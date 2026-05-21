import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from app.core.config import settings
from app.core.compliance_provider import decision_to_dict, get_gateway
from app.core.drift_registry import registry as drift_registry
from app.core.meta_engine import MetaSignalEngine
from app.core.scoring import build_signal_response
from app.db.repository import signal_repository
from app.services.event_publisher import publisher
from app.services.external_data_client import ExternalDataClient
from app.services.feature_store_client import FeatureStoreClient
from app.services.market_data_client import MarketDataClient
from app.services.strategy_registry_client import StrategyRegistryClient
from shared.health import check_redis, check_sql, check_tcp, health_payload

signals_evaluated_total = Counter(
    "signals_evaluated_total",
    "Total signals evaluated",
    ["direction"],
)
meta_signals_evaluated_total = Counter(
    "meta_signals_evaluated_total",
    "Total meta-ensemble signals evaluated",
    ["direction", "regime"],
)
drift_alert_level = Gauge(
    "meta_drift_alert_level",
    "Live-vs-backtest drift alert level (0=ok,1=warn,2=breach)",
    ["asset"],
)
drift_observations_total = Counter(
    "meta_drift_observations_total",
    "Trade returns observed by live drift monitor",
    ["asset"],
)
compliance_decisions_total = Counter(
    "meta_compliance_decisions_total",
    "Pre-trade compliance decisions on meta signals",
    ["asset", "approved", "reason"],
)
_DRIFT_LEVEL_MAP = {"ok": 0, "warn": 1, "breach": 2}

# G4: per-asset data-staleness guard. The existing SIGNAL_STALENESS_SECONDS in
# crypto-agent gates on the *signal* timestamp; this gate watches the candle
# timestamp directly so a stalled venue feed doesn't silently produce stale
# signals while feature-store keeps serving the last cached row.
SIGNAL_DATA_STALENESS_SECONDS = int(os.environ.get("SIGNAL_DATA_STALENESS_SECONDS", "300"))
SIGNAL_BLOCK_ON_STALE_DATA = os.environ.get("SIGNAL_BLOCK_ON_STALE_DATA", "false").lower() == "true"

signal_data_staleness_seconds = Gauge(
    "signal_data_staleness_seconds",
    "Age of the latest candle (seconds) at signal evaluation time, per asset.",
    ["asset"],
)
signals_skipped_stale_data_total = Counter(
    "signals_skipped_stale_data_total",
    "Signal evaluations rejected because candle data was stale.",
    ["asset"],
)

_g4_log = logging.getLogger("signal-service.staleness")

router = APIRouter()
client = FeatureStoreClient(base_url=settings.feature_store_base_url)
external_client = ExternalDataClient(base_url=settings.external_data_service_base_url)
strategy_client = StrategyRegistryClient(settings.strategy_registry_base_url)

market_data_client = MarketDataClient(base_url=settings.market_data_base_url)
meta_engine = MetaSignalEngine(market_data_client)


@router.get("/health")
def health() -> dict:
    return health_payload(
        "signal-service",
        {
            "timescaledb": check_sql("timescaledb", settings.timescale_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/signals/evaluate/{asset}")
def evaluate_signal(asset: str, x_user_id: str | None = Header(default=None)):
    # G4: data-staleness guard — fail fast on dead venue feed.
    candle_ts = market_data_client.get_latest_timestamp(asset)
    if candle_ts is not None:
        age = (datetime.now(timezone.utc) - candle_ts).total_seconds()
        signal_data_staleness_seconds.labels(asset=asset).set(age)
        if age > SIGNAL_DATA_STALENESS_SECONDS:
            _g4_log.warning(
                "stale candle for %s: age=%.0fs limit=%ds block=%s",
                asset, age, SIGNAL_DATA_STALENESS_SECONDS, SIGNAL_BLOCK_ON_STALE_DATA,
            )
            if SIGNAL_BLOCK_ON_STALE_DATA:
                signals_skipped_stale_data_total.labels(asset=asset).inc()
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "candle_stale",
                        "asset": asset,
                        "age_seconds": round(age),
                        "limit_seconds": SIGNAL_DATA_STALENESS_SECONDS,
                    },
                )
    else:
        signal_data_staleness_seconds.labels(asset=asset).set(float("inf"))

    features = client.get_latest_features(asset)
    external_context = external_client.get_external_context(asset)
    asset_type = "crypto" if asset.endswith("USDT") or asset.endswith("KRW") else "stock"
    strategy = strategy_client.get_active_strategy(asset_type, user_id=x_user_id)
    thresholds = {} if strategy is None else strategy.get("thresholds", {})
    evaluation = build_signal_response(
        asset=asset,
        features=features,
        threshold=settings.signal_threshold,
        entry_threshold=thresholds.get("entry"),
        exit_threshold=thresholds.get("exit"),
        asset_type=asset_type,
        strategy_id=None if strategy is None else strategy.get("id"),
        strategy_user_id=None if strategy is None else strategy.get("user_id"),
        external_context=external_context,
        external_signal_weight=settings.external_signal_weight,
    )
    signal_repository.save(asset=asset, evaluation=evaluation)
    signals_evaluated_total.labels(direction=evaluation.direction).inc()
    if evaluation.threshold_crossed:
        publisher.publish_threshold(asset=asset, asset_type=asset_type, evaluation=evaluation)
    return evaluation


@router.get("/signals/{asset}/latest")
def get_latest_signal(asset: str, x_user_id: str | None = Header(default=None)):
    evaluation = signal_repository.get_latest(asset, user_id=x_user_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="signal_not_found")
    return evaluation


@router.get("/signals")
def list_latest_signals(x_user_id: str | None = Header(default=None)):
    return signal_repository.list_latest(user_id=x_user_id)


@router.get("/signals/{asset}/history")
def get_signal_history(
    asset: str,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    x_user_id: str | None = Header(default=None),
):
    history = signal_repository.get_history(asset, user_id=x_user_id)
    if from_ts is not None:
        history = [item for item in history if item.feature_timestamp >= from_ts]
    if to_ts is not None:
        history = [item for item in history if item.feature_timestamp <= to_ts]
    return history


# ── Alpha catalog (Phase A observability) ─────────────────────────────
# Surfaces the IC engine's internal view: current weights, per-factor
# rolling stats, and the regime-conditional shadow pool. Backs Grafana
# dashboards and the /alpha panel in the UI.


@router.get("/alpha/catalog")
def alpha_catalog():
    """Return aggregate + regime-conditional alpha statistics."""
    try:
        from shared.factors.ic_weight_engine import get_ic_engine
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"ic_engine_unavailable": str(exc)})
    engine = get_ic_engine()
    engine.get_weights()  # warm-load from Redis on first call
    return {
        "weights": engine.get_weights(),
        "factors": engine.get_all_states(),
        "regimes": engine.get_regime_summary(),
    }


# ── Meta-ensemble signal (Phases F-J) ────────────────────────────────
# Parallel scoring path that runs shared.alpha alphas through the
# MV + DD + Kelly combiner. Gated by SIGNAL_META_ENABLED so it can
# ship dark before we cut traffic over. Same asset query shape as
# /signals/evaluate so callers can A/B test.


@router.post("/signals/meta/evaluate/{asset}")
def meta_evaluate_signal(asset: str, x_user_id: str | None = Header(default=None)):
    if not meta_engine.enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "reason": "meta_signal_disabled",
                "hint": "set SIGNAL_META_ENABLED=true to activate",
            },
        )
    result = meta_engine.evaluate(asset, threshold=settings.signal_threshold)
    meta_signals_evaluated_total.labels(
        direction=result.get("direction", "HOLD"),
        regime=result.get("regime", "unknown"),
    ).inc()

    # Pre-trade compliance check (attached to response; does NOT block 200).
    # Downstream order-service reads `compliance.approved` and skips if false.
    direction = result.get("direction", "HOLD")
    position = float(result.get("target_position") or result.get("position") or 0.0)
    if direction != "HOLD" and position != 0.0:
        try:
            gw = get_gateway(user_id=x_user_id)
            equity = gw._state.get_equity()  # noqa: SLF001
            notional = abs(position) * equity
            side = "BUY" if position > 0 else "SELL"
            decision = gw.check(symbol=asset, side=side, order_notional=notional)
            result["compliance"] = decision_to_dict(decision)
            compliance_decisions_total.labels(
                asset=asset,
                approved=str(decision.approved).lower(),
                reason=decision.reason,
            ).inc()
            if not decision.approved:
                from shared.logging import get_logger
                get_logger("signal-service").warning(
                    "compliance_blocked",
                    extra={
                        "service": "signal-service",
                        "asset": asset,
                        "side": side,
                        "notional": round(notional, 2),
                        "equity": round(equity, 2),
                        "reason": decision.reason,
                        "checks": decision.checks,
                        "user_id": x_user_id,
                    },
                )
        except Exception as exc:
            result["compliance"] = {"approved": True, "reason": "check_failed_open",
                                    "error": str(exc)[:200]}
    else:
        result["compliance"] = {"approved": True, "reason": "hold_or_flat"}

    # Exec cost estimate — lightweight static model based on maker_simulator defaults.
    # Gives UI/bots a quick read on whether the signal is worth executing.
    if direction != "HOLD" and position != 0.0:
        # Exchange-configurable cost estimates; env vars allow per-deployment tuning
        # without code changes. Still conservative defaults if unconfigured.
        maker_rebate_bps = float(os.getenv("EXEC_MAKER_REBATE_BPS", "-1.0"))
        _taker_fee = float(os.getenv("EXEC_TAKER_FEE_BPS", "4.0"))
        _half_spread = float(os.getenv("EXEC_HALF_SPREAD_BPS", "1.0"))
        _impact = float(os.getenv("EXEC_IMPACT_BPS", "2.0"))
        taker_cost_bps = _taker_fee + _half_spread + _impact
        maker_fill_prob = float(os.getenv("EXEC_MAKER_FILL_PROB", "0.85"))  # reduced from 0.95 — more realistic
        expected_cost_bps = (
            maker_fill_prob * maker_rebate_bps
            + (1 - maker_fill_prob) * taker_cost_bps
        )
        result["exec_cost_estimate"] = {
            "expected_bps": round(expected_cost_bps, 2),
            "maker_fill_probability": maker_fill_prob,
            "maker_rebate_bps": maker_rebate_bps,
            "taker_fallback_bps": round(taker_cost_bps, 1),
        }
    return result


@router.get("/signals/meta/status")
def meta_status():
    return {
        "enabled": meta_engine.enabled,
        "alphas": meta_engine.alpha_names,
    }


@router.post("/alpha/recompute")
def alpha_recompute():
    """Force-recompute IC weights. Useful after ingesting a batch of
    outcomes or during testing; in production the learning scheduler
    calls this on a fixed cadence."""
    from shared.factors.ic_weight_engine import get_ic_engine
    return {"weights": get_ic_engine().recompute_weights()}


# ── Alpha incubator (Phase C) ─────────────────────────────────────────
# Read-only surface on top of alpha_incubator_candidates. Submission and
# evaluation run via `scripts/incubate_alpha.py` because the 8yr backtest
# is CPU-heavy and we don't want API requests blocking uvicorn workers.


@router.get("/alpha/incubator")
def incubator_list(status: str | None = None, limit: int = 50):
    import psycopg  # type: ignore
    from shared.persistence import SqlStore

    dsn = settings.timescale_url
    # psycopg wants the raw "postgresql://" driver URL, not sqlalchemy's
    # "postgresql+psycopg://" form. Strip the dialect prefix if present.
    if dsn.startswith("postgresql+psycopg://"):
        dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    # The incubator table lives in the `platform` DB (auth user data live
    # there), not the `market` TSDB, so reroute the database name.
    import re
    dsn = re.sub(r"/market(\?|$)", r"/platform\1", dsn)

    sql = (
        "SELECT id, alpha_name, asset, status, sharpe_full, sharpe_oos, "
        "max_drawdown, ic, ic_ir, turnover, n_bars, submitted_at, "
        "evaluated_at, promoted_at, gate_report "
        "FROM alpha_incubator_candidates "
        + ("WHERE status = %s " if status else "")
        + "ORDER BY submitted_at DESC LIMIT %s"
    )
    params: list = [status] if status else []
    params.append(limit)
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d.name for d in cur.description]
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"incubator_unavailable": str(exc)[:200]})
    return [dict(zip(cols, r)) for r in rows]


# ── Black-Litterman views (portfolio optimization) ─────────────────────
# Exposes BL posterior weights for multi-asset allocation. Takes the
# meta-ensemble's directional signals as "views" and blends them with
# the equilibrium prior from market-cap weights.

@router.get("/signals/meta/views")
def bl_views():
    """Black-Litterman posterior weights for tracked assets."""
    try:
        import numpy as np
        from shared.portfolio.black_litterman import black_litterman
    except ImportError:
        raise HTTPException(status_code=503, detail="black_litterman_not_available")

    assets = drift_registry.known_assets()
    n = len(assets)
    if n < 2:
        raise HTTPException(status_code=400, detail="need_at_least_2_assets")

    # Fetch real market caps from external-data-service; fall back to equal weights.
    market_weights = None
    try:
        import httpx
        ext_url = os.getenv("EXTERNAL_DATA_SERVICE_BASE_URL", "http://localhost:8020")
        resp = httpx.get(f"{ext_url}/market/caps", params={"assets": ",".join(assets)}, timeout=3.0)
        if resp.status_code == 200:
            caps = resp.json()
            weights_arr = np.array([float(caps.get(a, 0)) for a in assets])
            if weights_arr.sum() > 0:
                market_weights = weights_arr / weights_arr.sum()
    except Exception:
        pass
    if market_weights is None:
        market_weights = np.ones(n) / n
    # Covariance: use backtest vol baselines as diagonal proxy
    from app.core.drift_registry import _BACKTEST_BASELINES
    vols = np.array([_BACKTEST_BASELINES.get(a, {}).get("vol", 0.01) for a in assets])
    sigma = np.diag(vols ** 2)

    # Views: meta-ensemble signals as relative views (P=identity, Q=signal scores)
    P = np.eye(n)
    Q = np.zeros(n)
    for i, asset in enumerate(assets):
        try:
            result = meta_engine.evaluate(asset, threshold=0.0)
            Q[i] = float(result.get("signal_score", 0.0)) * 0.01  # scale to return space
        except Exception:
            Q[i] = 0.0

    try:
        bl = black_litterman(sigma, market_weights, P, Q)
        return {
            "assets": assets,
            "posterior_weights": {a: round(float(w), 4) for a, w in zip(assets, bl.weights)},
            "implied_equilibrium": {a: round(float(r), 6) for a, r in zip(assets, bl.implied_equilibrium)},
            "posterior_returns": {a: round(float(r), 6) for a, r in zip(assets, bl.mu_posterior)},
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"bl_error": str(exc)[:200]})


@router.get("/alpha/incubator/promoted")
def incubator_promoted():
    """Short-hand alias for the UI / ensemble loader."""
    return incubator_list(status="PROMOTED")


# ── Live drift monitor (Phase M) ──────────────────────────────────────
# Compares realized per-bar returns against backtest-expected Sharpe
# for the meta-ensemble. Upstream (order-service / PnL job) posts
# observations; ops pages on the Prometheus gauge.


@router.post("/signals/meta/drift/{asset}/observe")
def drift_observe(asset: str, body: dict):
    ret = body.get("trade_return")
    if ret is None:
        raise HTTPException(status_code=400, detail="trade_return required")
    try:
        ret = float(ret)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="trade_return must be numeric")
    drift_registry.observe(asset, ret)
    drift_observations_total.labels(asset=asset).inc()
    alert = drift_registry.evaluate(asset)
    drift_alert_level.labels(asset=asset).set(_DRIFT_LEVEL_MAP.get(alert.level, 0))
    return {"asset": asset, "level": alert.level, "metrics": alert.metrics}


@router.get("/signals/meta/drift/{asset}")
def drift_status(asset: str):
    alert = drift_registry.evaluate(asset)
    drift_alert_level.labels(asset=asset).set(_DRIFT_LEVEL_MAP.get(alert.level, 0))
    return {
        "asset": asset,
        "level": alert.level,
        "reason": alert.reason,
        "psr_reject": alert.psr_reject,
        "metrics": alert.metrics,
    }


@router.get("/signals/meta/drift")
def drift_status_all():
    out = {}
    for a in drift_registry.known_assets():
        alert = drift_registry.evaluate(a)
        out[a] = {"level": alert.level, "metrics": alert.metrics}
    return out
