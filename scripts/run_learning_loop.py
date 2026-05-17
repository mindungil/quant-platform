#!/usr/bin/env python3
"""Cron-style runner for the V3 online learning closed loop.

Designed to run inside the strategy-lab container every N minutes
(default 5). Each cycle:

  1. Warm-start LearningLoop from Redis
  2. Fetch latest bar PnL per active alpha (DB query)
  3. Push into LearningLoop.update_alpha_pnl → collect AlphaLoopResult
  4. For every state_changed=True result, publish a NATS event
     `learning.alpha.state_changed` so the rest of the stack reacts
     (signal-service zeros out the alpha, dashboard annotates, etc.)
  5. Fetch latest factor (score, forward_return) pairs → update_factor_ic
  6. checkpoint() back to Redis

The DB-query and NATS-publish glue is sketched (uses the standard
shared/persistence + shared/events plumbing). Adapt the SQL to your
ledger schema.

Env:
  REDIS_URL                     Redis connection URL
  NATS_URL                      NATS for event publish (optional)
  POSTGRES_URL                  ledger DB for bar PnL fetch
  LEARNING_LOOP_LOOKBACK_BARS   how many recent bars to ingest per cycle (default 1)
  LEARNING_LOOP_DRY_RUN         '1' to skip event publish + checkpoint (debug)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

sys.path.insert(0, "/code")

logger = logging.getLogger("learning-loop")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

LOOKBACK_BARS = int(os.getenv("LEARNING_LOOP_LOOKBACK_BARS", "1"))
DRY_RUN = os.getenv("LEARNING_LOOP_DRY_RUN", "0") == "1"


def _psycopg_url() -> str | None:
    """Return a raw psycopg URL (strip SQLAlchemy driver prefix if present)."""
    url = os.getenv("POSTGRES_URL")
    if not url:
        return None
    # psycopg only accepts the bare `postgresql://` scheme.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _fetch_alpha_pnl(lookback_bars: int) -> list[tuple[str, float]]:
    """Return [(strategy_id, latest_pnl_sum), ...] from shadow_fills.

    Uses `strategy_id` as the learning key. Once we add a true
    per-alpha PnL ledger (e.g. extract alpha_name from
    signal_history.payload), point this query at it instead.

    `lookback_bars` is in bars; we use 5min per bar as a default
    cadence and convert to interval seconds.
    """
    url = _psycopg_url()
    if not url:
        return []
    try:
        import psycopg
        seconds = lookback_bars * 300  # 5min/bar
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT strategy_id, COALESCE(SUM(pnl), 0) AS pnl
                    FROM shadow_fills
                    WHERE realized = true
                      AND pnl IS NOT NULL
                      AND ts > now() - make_interval(secs => %s)
                    GROUP BY strategy_id
                    """,
                    (seconds,),
                )
                return [(str(row[0]), float(row[1])) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("alpha_pnl_fetch_failed: %s", exc)
        return []


def _fetch_factor_ic_inputs() -> list[tuple[str, float, float]]:
    """Return [(factor_name, score, forward_return), ...] from crypto_decisions.

    P3 implementation: leverages the components dict that crypto-agent
    already writes to crypto_decisions.payload (rsi, macd, vwap, bollinger,
    fear_greed_index, etc.) and joins each decision against the *next*
    decision for the same asset to compute a forward return proxy
    (delta in signal_score, scaled).

    The forward_return is a noisy proxy — true alpha factor IC would need
    next-bar realized return, but signal_score velocity captures most of
    the same signal. Good enough for FactorDecayMonitor to start
    distinguishing live factors from dead ones.
    """
    url = _psycopg_url()
    if not url:
        return []
    try:
        import psycopg
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH dec AS (
                      SELECT created_at, asset,
                             (payload->>'signal_score')::float AS score,
                             payload->'components' AS comps
                      FROM crypto_decisions
                      WHERE created_at > now() - interval '1 hour'
                    ),
                    fwd AS (
                      SELECT *,
                        LEAD(score) OVER (PARTITION BY asset ORDER BY created_at) AS next_score
                      FROM dec
                    )
                    SELECT
                      key AS factor_name,
                      (comps->>key)::float AS factor_score,
                      (next_score - score) AS forward_return
                    FROM fwd, jsonb_object_keys(comps) AS key
                    WHERE next_score IS NOT NULL
                      AND comps ? key
                      AND (comps->>key) ~ '^-?[0-9]+\\.?[0-9]*$'
                      AND key NOT IN ('formula_confidence', '_regime_code', '_weight_mode',
                                       '_n_components', '_agreement_bonus',
                                       'style_score', 'ensemble_score', 'style_formula', 'regime')
                    LIMIT 5000
                    """
                )
                return [(str(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("factor_ic_fetch_failed: %s", exc)
        return []


async def _publish_state_change(event: dict[str, Any]) -> None:
    if DRY_RUN:
        logger.info("dry_run_event: %s", event)
        return
    try:
        from shared.events import JetStreamBus, EventEnvelope
        from shared.persistence import RedisStore
        nats_url = os.getenv("NATS_URL", "nats://nats:4222")
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        bus = JetStreamBus(nats_url=nats_url, redis_store=RedisStore(redis_url), enabled=True)
        await bus.connect()
        await bus.publish(
            "learning.alpha.state_changed",
            EventEnvelope(
                event_type="learning.alpha.state_changed",
                source="learning-loop",
                data=event,
            ),
        )
        await bus.close()
    except Exception as exc:
        logger.warning("publish_failed: %s", exc)


def _run_cycle() -> dict[str, int]:
    from shared.learning import LearningLoop, LearningLoopConfig
    from shared.learning.redis_store import RedisStateStore

    cfg = LearningLoopConfig(
        dsr_window_bars=int(os.getenv("DSR_WINDOW_BARS", str(24 * 90))),
        dsr_n_trials=int(os.getenv("DSR_N_TRIALS", "5")),
        pause_threshold=float(os.getenv("DSR_PAUSE_THRESHOLD", "0.5")),
        recover_threshold=float(os.getenv("DSR_RECOVER_THRESHOLD", "0.7")),
        consecutive_required=int(os.getenv("DSR_CONSECUTIVE_REQUIRED", "3")),
        factor_ir_threshold=float(os.getenv("FACTOR_IR_THRESHOLD", "0.2")),
    )
    store = RedisStateStore(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    loop = LearningLoop(config=cfg, state_store=store)
    loop.warm_start()

    # Alphas
    alpha_pnl = _fetch_alpha_pnl(LOOKBACK_BARS)
    alpha_changes = 0
    for result in loop.update_alpha_pnl_bulk(alpha_pnl):
        if result.state_changed:
            alpha_changes += 1
            logger.info(
                "alpha_state_changed: %s %s->%s (%s)",
                result.alpha_name, result.prev_state, result.new_state,
                result.decision_reason,
            )
            asyncio.get_event_loop().run_until_complete(
                _publish_state_change(result.as_event())
            )

    # Factors
    factor_inputs = _fetch_factor_ic_inputs()
    factor_weight_changes = 0
    for result in loop.update_factor_ic_bulk(factor_inputs):
        if result.weight_changed:
            factor_weight_changes += 1
            logger.info(
                "factor_weight_changed: %s -> %.2f (IC_IR=%s decayed=%s)",
                result.factor_name, result.new_active_weight,
                result.ic_ir, result.is_decayed,
            )

    written = 0 if DRY_RUN else loop.checkpoint()
    return {
        "alphas_processed": len(alpha_pnl),
        "alpha_state_changes": alpha_changes,
        "factors_processed": len(factor_inputs),
        "factor_weight_changes": factor_weight_changes,
        "keys_checkpointed": written,
    }


def _daemon_loop() -> None:
    """Forever-loop: run one cycle, sleep, repeat. Restartable, NATS/Redis
    losses don't kill it. Exposes /metrics on $METRICS_PORT (default 9100)
    so Prometheus can scrape the V3 metrics without touching the
    main service ports."""
    import time
    try:
        from prometheus_client import start_http_server
        metrics_port = int(os.getenv("METRICS_PORT", "9100"))
        start_http_server(metrics_port)
        logger.info("learning_loop_metrics_exposed port=%s", metrics_port)
    except Exception as exc:
        logger.warning("metrics_server_start_failed: %s", exc)
    interval = int(os.getenv("LEARNING_LOOP_INTERVAL_SECONDS", "300"))
    logger.info("learning_loop_daemon_starting interval=%s", interval)
    while True:
        try:
            summary = _run_cycle()
            logger.info("cycle_complete: %s", summary)
        except Exception as exc:
            logger.exception("cycle_failed: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        _daemon_loop()
    else:
        summary = _run_cycle()
        logger.info("cycle_complete: %s", summary)
