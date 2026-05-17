#!/usr/bin/env python3
"""V4-3 GP Alpha Discovery cron — runs every hour, registers winners.

Each cycle:
  1. Pull last 7d of crypto_decisions.components (the live factor scores)
     and the asset's bar returns from market-data.
  2. Run gp_miner.evolve(factors, features, forward_returns) for N generations.
  3. For each top-K candidate, run passes_gate(); persist winners to Redis
     under gp:winner:<hash> and auto-register them into ALPHA_REGISTRY as
     gp_<hash>.

When wired in production, persistence + ALPHA_REGISTRY register hooks let
the trading agent immediately consider the new alpha in the next decision
cycle — alpha discovery → live trading inside an hour, with the
institutional gate enforced.

Reference impl — data fetch + registration adapters are sketched. The
gp_miner core is the real value; this script proves the harness.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, "/code")

logger = logging.getLogger("gp-discovery")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

INTERVAL = int(os.getenv("GP_DISCOVERY_INTERVAL_SECONDS", "3600"))
POPULATION = int(os.getenv("GP_POPULATION", "30"))
GENERATIONS = int(os.getenv("GP_GENERATIONS", "10"))
MIN_SHARPE = float(os.getenv("GP_MIN_SHARPE", "1.0"))
DRY_RUN = os.getenv("GP_DISCOVERY_DRY_RUN", "1") == "1"


def _psycopg_url() -> str | None:
    url = os.getenv("POSTGRES_URL")
    if not url:
        return None
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _fetch_features_and_returns():
    """Return (features_df, forward_returns_series).

    Strategy: pull (created_at, components, signal_score) from
    crypto_decisions for the last 7d, then use signal_score velocity as
    a forward-return proxy (same trick as the factor IC ledger).
    """
    url = _psycopg_url()
    if not url:
        return None, None
    try:
        import psycopg
        import pandas as pd
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT created_at, asset,
                           (payload->>'signal_score')::float AS score,
                           payload->'components' AS comps
                    FROM crypto_decisions
                    WHERE created_at > now() - interval '7 days'
                      AND asset = 'BTCUSDT'
                    ORDER BY created_at
                    """
                )
                rows = cur.fetchall()
        if len(rows) < 100:
            logger.warning("not_enough_data rows=%s", len(rows))
            return None, None
        # Build features DataFrame from the components dict
        records = []
        scores = []
        for ts, _asset, score, comps in rows:
            rec = {"timestamp": ts}
            for k, v in (comps or {}).items():
                if k.startswith("_") or k in ("formula_confidence", "style_score",
                                                "ensemble_score", "style_formula",
                                                "regime"):
                    continue
                try:
                    rec[k] = float(v)
                except (TypeError, ValueError):
                    continue
            records.append(rec)
            scores.append(float(score))
        features = pd.DataFrame(records).set_index("timestamp").sort_index()
        forward_returns = pd.Series(scores, index=features.index).diff().fillna(0.0)
        return features, forward_returns
    except Exception as exc:
        logger.warning("fetch_failed: %s", exc)
        return None, None


def _persist_winner(tree_str: str, sharpe: float, diag: dict) -> None:
    """Write a winner to Redis. Auto-register hook left to the consumer."""
    try:
        import redis
        r = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
            socket_timeout=2,
        )
        h = hashlib.md5(tree_str.encode()).hexdigest()[:8]
        key = f"gp:winner:{h}"
        r.set(key, json.dumps({
            "tree": tree_str, "sharpe": sharpe, "diag": diag,
            "discovered_at": time.time(),
        }))
        r.expire(key, 30 * 24 * 3600)  # 30d TTL
        logger.info("winner_persisted name=gp_%s sharpe=%.3f", h, sharpe)
    except Exception as exc:
        logger.warning("persist_failed: %s", exc)


def _run_cycle() -> dict:
    from shared.alpha.discovery.gp_miner import (
        GPConfig, evolve, passes_gate,
    )
    features, forward_returns = _fetch_features_and_returns()
    if features is None or features.empty:
        return {"status": "no_data"}

    factors = list(features.columns)
    cfg = GPConfig(
        population_size=POPULATION, n_generations=GENERATIONS,
        max_tree_depth=3, seed=int(time.time()) % 10_000,
    )
    result = evolve(factors=factors, features=features,
                    forward_returns=forward_returns, config=cfg)

    summary = {
        "best_sharpe": result.best_sharpe,
        "best_tree": result.best_str,
        "best_hash": result.best_hash,
        "n_factors": len(factors),
        "history_min": min(result.history),
        "history_max": max(result.history),
    }

    # Gate check
    if result.best_sharpe >= MIN_SHARPE:
        ok, diag = passes_gate(result.best_tree, features, forward_returns,
                                min_sharpe=MIN_SHARPE)
        summary.update({"gate_passed": ok, "gate_diag": diag})
        if ok and not DRY_RUN:
            _persist_winner(result.best_str, result.best_sharpe, diag)
        elif ok and DRY_RUN:
            logger.info("dry_run_winner sharpe=%.3f tree=%s",
                        result.best_sharpe, result.best_str)
    else:
        summary["gate_passed"] = False
        summary["reason"] = "below_min_sharpe"

    return summary


def _daemon() -> None:
    try:
        from prometheus_client import start_http_server, Counter, Gauge
        port = int(os.getenv("METRICS_PORT", "9102"))
        start_http_server(port)
        logger.info("gp_discovery_metrics_exposed port=%s", port)
        global _GP_CYCLES, _GP_BEST_SHARPE
        _GP_CYCLES = Counter("quant_v4_gp_cycles_total",
                              "GP discovery cycles completed", ["status"])
        _GP_BEST_SHARPE = Gauge("quant_v4_gp_best_sharpe",
                                  "Best Sharpe of the most recent GP run.")
    except Exception as exc:
        logger.warning("metrics_server_start_failed: %s", exc)
        _GP_CYCLES = _GP_BEST_SHARPE = None  # type: ignore[assignment]

    logger.info("gp_discovery_daemon_starting interval=%s dry_run=%s",
                 INTERVAL, DRY_RUN)
    while True:
        try:
            summary = _run_cycle()
            logger.info("cycle_complete: %s",
                         {k: v for k, v in summary.items() if k != "best_tree"})
            if _GP_BEST_SHARPE is not None and "best_sharpe" in summary:
                _GP_BEST_SHARPE.set(float(summary["best_sharpe"]))
            if _GP_CYCLES is not None:
                _GP_CYCLES.labels(status=summary.get("status", "ok")).inc()
        except Exception as exc:
            logger.exception("cycle_failed: %s", exc)
            if _GP_CYCLES is not None:
                _GP_CYCLES.labels(status="error").inc()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        _daemon()
    else:
        s = _run_cycle()
        logger.info("cycle_complete: %s", s)
