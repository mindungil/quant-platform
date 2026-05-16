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


def _fetch_alpha_pnl(lookback_bars: int) -> list[tuple[str, float]]:
    """Return [(alpha_name, latest_bar_pnl), ...] from the ledger.

    Replace the SQL with your actual ledger schema. The expected output
    is a single (alpha_name, pnl) tuple per active alpha per cycle.
    """
    try:
        import psycopg
        url = os.getenv("POSTGRES_URL")
        if not url:
            return []
        with psycopg.connect(url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT alpha_name, sum(bar_pnl) as pnl
                    FROM alpha_bar_pnl
                    WHERE bar_ts > now() - interval '%s minutes'
                    GROUP BY alpha_name
                    """,
                    (lookback_bars * 5,),
                )
                return [(row[0], float(row[1])) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("alpha_pnl_fetch_failed: %s", exc)
        return []


def _fetch_factor_ic_inputs() -> list[tuple[str, float, float]]:
    """Return [(factor_name, latest_score, latest_forward_return), ...].

    Same as above — adapt the SQL to your factor scoring ledger.
    """
    try:
        import psycopg
        url = os.getenv("POSTGRES_URL")
        if not url:
            return []
        with psycopg.connect(url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT factor_name, score, forward_return
                    FROM factor_scoring_ledger
                    WHERE bar_ts > now() - interval '15 minutes'
                    """
                )
                return [(row[0], float(row[1]), float(row[2])) for row in cur.fetchall()]
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


if __name__ == "__main__":
    summary = _run_cycle()
    logger.info("cycle_complete: %s", summary)
