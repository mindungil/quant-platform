#!/usr/bin/env python3
"""D8 — Attribution per-cycle daemon.

Every N minutes:
  1. Pull recent fills + alpha decisions from postgres.
  2. Rebuild a synthetic AttributionReport (per-alpha cumulative PnL).
  3. Push to Prometheus gauges (already declared in shared.observability_v3).
  4. Persist to Redis so Grafana panels survive daemon restarts.

This closes the observability loop for "which alpha actually made me
money" — V3 attribution.py code existed but was never being called in
production. Now it runs autonomously on a 5-min cadence.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, "/code")

logger = logging.getLogger("attribution-daemon")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

INTERVAL = int(os.getenv("ATTRIBUTION_INTERVAL_SECONDS", "300"))
LOOKBACK_HOURS = int(os.getenv("ATTRIBUTION_LOOKBACK_HOURS", "24"))


def _psycopg_url() -> str | None:
    url = os.getenv("POSTGRES_URL")
    if not url:
        return None
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _fetch_decision_pnl() -> dict[str, float]:
    """Aggregate per-alpha cumulative reward proxy from crypto_decisions.

    Each decision's signal_score serves as a proxy for that decision's
    expected edge. Cumulative sum per (strategy_id, formula) gives a
    quick attribution that's stable enough for a Grafana view while we
    wait for real fill-level PnL to accumulate (after D10).
    """
    url = _psycopg_url()
    if not url:
        return {}
    try:
        import psycopg
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      COALESCE(payload->'components'->>'style_formula', 'unknown') AS alpha,
                      SUM((payload->>'signal_score')::float) AS cum_score,
                      COUNT(*) AS n_decisions,
                      AVG((payload->>'signal_score')::float) AS avg_score,
                      SUM(CASE WHEN payload->>'action' = 'BUY' THEN 1 ELSE 0 END) AS n_buy,
                      SUM(CASE WHEN payload->>'action' = 'SELL' THEN 1 ELSE 0 END) AS n_sell
                    FROM crypto_decisions
                    WHERE created_at > now() - make_interval(hours => %s)
                    GROUP BY 1
                    ORDER BY 2 DESC
                    LIMIT 100
                    """,
                    (LOOKBACK_HOURS,),
                )
                rows = cur.fetchall()
        # Returns {alpha_name: cumulative_score_proxy}
        return {str(r[0]): float(r[1] or 0.0) for r in rows if r[1] is not None}
    except Exception as exc:
        logger.warning("fetch_failed: %s", exc)
        return {}


def _persist(snapshot: dict) -> None:
    try:
        import redis
        r = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True, socket_timeout=2,
        )
        r.set("attribution:latest", json.dumps(snapshot))
        r.expire("attribution:latest", 24 * 3600)
    except Exception as exc:
        logger.debug("redis_persist_failed: %s", exc)


def _export_metrics(snapshot: dict[str, float]) -> None:
    """Push to quant_v3_attribution_alpha_cumulative_pnl gauge."""
    try:
        from shared.observability_v3 import ATTRIBUTION_ALPHA_CUMULATIVE_PNL
        for alpha, cum in snapshot.items():
            ATTRIBUTION_ALPHA_CUMULATIVE_PNL.labels(alpha_name=alpha).set(float(cum))
    except Exception as exc:
        logger.debug("metric_export_failed: %s", exc)


def _run_cycle() -> dict:
    snap = _fetch_decision_pnl()
    _export_metrics(snap)
    _persist({"timestamp": time.time(), "alphas": snap})
    return {
        "n_alphas": len(snap),
        "top": dict(list(sorted(snap.items(), key=lambda kv: kv[1], reverse=True))[:5]),
    }


def _daemon() -> None:
    try:
        from prometheus_client import start_http_server
        port = int(os.getenv("METRICS_PORT", "9104"))
        start_http_server(port)
        logger.info("attribution_metrics_exposed port=%s", port)
    except Exception as exc:
        logger.warning("metrics_start_failed: %s", exc)
    logger.info("attribution_daemon_starting interval=%s lookback_hours=%s",
                INTERVAL, LOOKBACK_HOURS)
    while True:
        try:
            summary = _run_cycle()
            logger.info("cycle_complete: %s", summary)
        except Exception as exc:
            logger.exception("cycle_failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        _daemon()
    else:
        s = _run_cycle()
        logger.info("cycle_complete: %s", s)
