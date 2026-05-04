"""Learning Scheduler — 3-tier autonomous self-improvement engine.

Fast (every 5min):  Verify past decisions against price -> MAB + IC feedback
Daily (every 24h):  Optimize factor weights from verified data + IC recompute
Weekly (every 7d):  Meta-learning: IC-based factor culling, protocol adjustment

IC Engine Integration:
  - Fast loop feeds raw (factor_scores, forward_return) pairs to IC engine
  - Daily loop recomputes IC weights and persists to Redis
  - Weekly loop uses IC_IR to cull unstable factors
  - Signal-service reads IC weights from Redis at scoring time
  Reference: Grinold & Kahn (2000), Lopez de Prado (2018)
"""
import asyncio
import math
import time
import os
from datetime import datetime, timezone, timedelta

import httpx

from shared.logging import get_logger

# Use the shared "crypto-agent" logger so structured JSON logs are visible
# (the previous logging.getLogger("learning-scheduler") had no handler attached
# to the JSON pipeline and silently dropped every record).
logger = get_logger("crypto-agent")

MARKET_DATA_URL = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")
AGENT_URL = os.getenv("CRYPTO_AGENT_BASE_URL", "http://localhost:8006")
MEMORY_URL = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
STATISTICS_URL = os.getenv("STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")
EXTERNAL_DATA_URL = os.getenv("EXTERNAL_DATA_SERVICE_BASE_URL", "http://localhost:8020")


class LearningScheduler:
    def __init__(self):
        self._running = False
        self._fast_interval = 300  # 5 minutes
        self._daily_counter = 0
        self._weekly_counter = 0
        self._daily_threshold = 288  # 288 * 5min = 24h
        self._weekly_threshold = 2016  # 2016 * 5min = 7d

    async def start(self):
        self._running = True
        logger.info("learning_scheduler_started")
        while self._running:
            try:
                await self._fast_loop()
                self._daily_counter += 1
                self._weekly_counter += 1

                if self._daily_counter >= self._daily_threshold:
                    await self._daily_loop()
                    self._daily_counter = 0

                if self._weekly_counter >= self._weekly_threshold:
                    await self._weekly_loop()
                    self._weekly_counter = 0

            except Exception:
                logger.exception("learning_loop_error")

            await asyncio.sleep(self._fast_interval)

    def stop(self):
        self._running = False

    async def _fast_loop(self):
        """Every 5 min: verify past decisions -> MAB feedback."""
        try:
            # 1. Get decisions from recent history
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{AGENT_URL}/decisions/history/BTCUSDT?limit=50")
                if resp.status_code != 200:
                    logger.debug("fast_loop_no_decisions", extra={"status": resp.status_code})
                    return
                decisions = resp.json()

            if not decisions:
                return

            # 2. Get current price
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{MARKET_DATA_URL}/candles/BTCUSDT/latest")
                if resp.status_code != 200:
                    logger.debug("fast_loop_no_price", extra={"status": resp.status_code})
                    return
                current_price = resp.json().get("close", 0)

            if current_price <= 0:
                return

            # 3. Verify decisions that are 1-48 hours old
            verified_count = 0
            try:
                from app.core.mab_state import formula_mab
            except Exception as e:
                logger.debug("fast_loop_mab_import_failed", extra={"error": str(e)[:100]})
                return

            correct = 0
            total_evaluated = 0

            for d in decisions:
                ref_price = d.get("reference_price")
                if not ref_price or ref_price <= 0:
                    continue

                # Check age
                try:
                    ts_raw = d.get("timestamp", "")
                    if isinstance(ts_raw, str):
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    else:
                        continue
                    age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                except Exception:
                    continue

                if age_hours < 1 or age_hours > 48:
                    continue  # too recent or too old

                # Compute price change
                price_change_pct = ((current_price - ref_price) / ref_price) * 100
                action = d.get("action", "HOLD")

                # Determine reward
                if action == "BUY":
                    reward = price_change_pct / 5.0  # 5% = full reward
                elif action == "SELL":
                    reward = -price_change_pct / 5.0
                else:  # HOLD
                    reward = max(0, 1 - abs(price_change_pct) / 5.0) * 0.3  # small reward for correct hold

                reward = max(-1.0, min(1.0, reward))

                # Get formula name + regime from components (now embedded by graph.py)
                components = d.get("components") or {}
                formula_name = components.get("style_formula", "composite_adaptive")
                regime = components.get("regime") or d.get("regime") or ""

                # Update MAB
                try:
                    formula_mab.update_from_hindsight(formula_name, price_change_pct, regime=regime)
                    verified_count += 1
                except Exception:
                    pass

                # Track accuracy with asymmetric HOLD scoring (matches hindsight.py).
                # HOLD only counts (correct OR incorrect) when the market was actually
                # quiet; otherwise it's neutral and excluded from the denominator.
                if action == "BUY":
                    total_evaluated += 1
                    if current_price > ref_price:
                        correct += 1
                elif action == "SELL":
                    total_evaluated += 1
                    if current_price < ref_price:
                        correct += 1
                else:  # HOLD
                    quiet = abs(current_price - ref_price) / ref_price < 0.015
                    if quiet:
                        total_evaluated += 1
                        correct += 1
                    # Non-quiet HOLDs: neither correct nor incorrect — skipped

            if verified_count > 0:
                logger.info("fast_loop_complete", extra={"verified": verified_count, "current_price": current_price})
                # Persist MAB state after each batch — ensures restart-survival
                try:
                    formula_mab.force_save()
                except Exception as exc:
                    logger.warning("fast_loop_mab_save_failed", extra={"error": str(exc)[:120]})

            # ── IC Engine: feed verified decisions for rolling IC tracking ──
            ic_updated = 0
            ic_skipped = 0
            try:
                from shared.factors.ic_weight_engine import get_ic_engine
                ic_engine = get_ic_engine()
                _IC_META_KEYS = frozenset({
                    "ensemble_score", "style_score", "style_formula",
                    "formula_confidence", "regime", "adx_filter",
                    "_n_components", "_insufficient_data", "_agreement_bonus",
                    "_weight_mode",
                })
                # Same per-factor dedupe rationale as the daily loop:
                # bursts of decisions on the same hourly bar would feed
                # identical (score, fwd_return) pairs to slowly-updating
                # factors and inflate IC toward ±1.0.
                seen: dict[str, set[tuple[float, float]]] = {}
                for d in decisions:
                    ref_price = d.get("reference_price")
                    if not ref_price or ref_price <= 0:
                        continue
                    try:
                        ts_raw = d.get("timestamp", "")
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                    except Exception:
                        continue
                    if age_h < 1 or age_h > 48:
                        continue
                    forward_return = (current_price - ref_price) / ref_price
                    components = d.get("components") or {}
                    factor_scores = {
                        k: float(v) for k, v in components.items()
                        if isinstance(v, (int, float))
                        and math.isfinite(float(v))
                        and not k.startswith("cat_")
                        and k not in _IC_META_KEYS
                    }
                    if not factor_scores:
                        continue
                    deduped = {}
                    for fname, score in factor_scores.items():
                        key = (round(score, 8), round(forward_return, 8))
                        bucket = seen.setdefault(fname, set())
                        if key in bucket:
                            ic_skipped += 1
                            continue
                        bucket.add(key)
                        deduped[fname] = score
                    if deduped:
                        ic_engine.update(deduped, forward_return)
                        ic_updated += 1
                if ic_updated > 0:
                    logger.info("fast_loop_ic_fed", extra={
                        "observations": ic_updated, "deduped": ic_skipped,
                    })
            except Exception as exc:
                logger.debug("fast_loop_ic_feed_failed", extra={"error": str(exc)[:120]})

            # Update accuracy metric and select protocol based on conditions
            if total_evaluated > 0:
                try:
                    from shared.factors.dynamic_weights import set_recent_accuracy, set_active_protocol
                    accuracy = correct / total_evaluated
                    set_recent_accuracy(accuracy)
                    logger.info("accuracy_updated", extra={"accuracy": round(accuracy, 4), "total": total_evaluated})

                    # Get market conditions for protocol selection
                    fear_greed = 50
                    try:
                        async with httpx.AsyncClient(timeout=5) as client:
                            resp = await client.get("{EXTERNAL_DATA_URL}/external/context/BTCUSDT")
                            if resp.status_code == 200:
                                ext = resp.json()
                                fear_greed = ext.get("fear_greed_index", 50)
                    except Exception:
                        pass

                    from app.core.protocol_router import select_protocol
                    protocol = select_protocol(accuracy, fear_greed)
                    set_active_protocol(protocol["name"])
                    logger.info("fast_loop_protocol_updated", extra={"protocol": protocol["name"]})

                except Exception as e:
                    logger.debug("accuracy_update_failed", extra={"error": str(e)[:100]})

        except Exception as e:
            logger.warning("fast_loop_failed", extra={"error": str(e)[:200]})

    async def _daily_loop(self):
        """Every 24h: optimize factor weights from real verified decisions.

        Pulls the last 7 days of decisions across the monitored asset universe,
        anchors each one against current price (>= 1h elapsed), enriches with
        the actual price_change_pct, then runs the factor + category optimizer.
        """
        try:
            from shared.factors.dynamic_weights import (
                load_factor_weights, save_factor_weights,
                load_category_weights, save_category_weights,
            )
            from shared.factors.optimizer import optimize_factor_weights, optimize_category_weights
            from shared.factors.registry import ALL_FACTORS

            f2c = {f.name: f.category for f in ALL_FACTORS}

            assets = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
            verified: list[dict] = []

            async with httpx.AsyncClient(timeout=15) as client:
                for asset in assets:
                    # Pull recent decisions (with components)
                    try:
                        d_resp = await client.get(
                            f"{AGENT_URL}/decisions/history/{asset}?limit=500"
                        )
                        if d_resp.status_code != 200:
                            continue
                        decisions = d_resp.json() or []
                    except Exception:
                        continue

                    # Pull current price as the truth anchor
                    try:
                        p_resp = await client.get(f"{MARKET_DATA_URL}/candles/{asset}/latest")
                        if p_resp.status_code != 200:
                            continue
                        cur_price = p_resp.json().get("close", 0)
                    except Exception:
                        continue
                    if not cur_price or cur_price <= 0:
                        continue

                    # Enrich decisions with realized price_change_pct
                    for d in decisions:
                        ref = d.get("reference_price")
                        if not ref or ref <= 0:
                            continue
                        ts_raw = d.get("timestamp", "")
                        try:
                            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                        except Exception:
                            continue
                        if age_h < 1 or age_h > 168:  # 1h .. 7d window
                            continue
                        d_enriched = dict(d)
                        d_enriched["price_change_pct"] = ((cur_price - ref) / ref) * 100
                        verified.append(d_enriched)

            if len(verified) < 10:
                logger.info("daily_loop_skip_insufficient_data", extra={"verified": len(verified)})
                return

            current_fw = load_factor_weights()
            new_fw = optimize_factor_weights(verified, current_fw)
            save_factor_weights(new_fw)

            current_cw = load_category_weights()
            new_cw = optimize_category_weights(verified, current_cw, f2c)
            save_category_weights(new_cw)

            # ── IC Engine: bulk update + recompute weights ──
            # This is the critical daily recompute that makes signal-service
            # switch from heuristic (_weight_mode=0) to IC-driven (_weight_mode=1).
            ic_n_factors = 0
            ic_active = 0
            try:
                from shared.factors.ic_weight_engine import get_ic_engine
                ic_engine = get_ic_engine()

                # Restore snapshot if engine is cold (first run after restart)
                if not ic_engine._states:
                    ic_engine.load_state_snapshot()

                _IC_META_KEYS = frozenset({
                    "ensemble_score", "style_score", "style_formula",
                    "formula_confidence", "regime", "adx_filter",
                    "_n_components", "_insufficient_data", "_agreement_bonus",
                    "_weight_mode",
                })

                # Dedupe per-factor before feeding the IC engine. Without
                # this, a burst of decisions sharing the same hourly bar
                # (same reference_price → same forward_return; slowly-changing
                # factors like news_sentiment/fear_greed_index also stuck on
                # the same scalar) gets fed as N "independent" observations,
                # which inflates rolling-IC ranks toward ±1.0 from the few
                # non-tied points and crowds out informative factors.
                seen: dict[str, set[tuple[float, float]]] = {}
                ic_obs_fed = 0
                ic_obs_skipped = 0
                for d in verified:
                    forward_return = d.get("price_change_pct", 0.0) / 100.0
                    if not math.isfinite(forward_return):
                        continue
                    components = d.get("components") or {}
                    factor_scores = {
                        k: float(v) for k, v in components.items()
                        if isinstance(v, (int, float))
                        and math.isfinite(float(v))
                        and not k.startswith("cat_")
                        and k not in _IC_META_KEYS
                    }
                    if not factor_scores:
                        continue
                    deduped = {}
                    for fname, score in factor_scores.items():
                        key = (round(score, 8), round(forward_return, 8))
                        bucket = seen.setdefault(fname, set())
                        if key in bucket:
                            ic_obs_skipped += 1
                            continue
                        bucket.add(key)
                        deduped[fname] = score
                    if deduped:
                        ic_engine.update(deduped, forward_return)
                        ic_obs_fed += 1

                logger.info("daily_ic_dedupe", extra={
                    "fed": ic_obs_fed, "skipped_duplicates": ic_obs_skipped,
                })

                # Recompute weights → saves to Redis automatically
                new_ic_weights = ic_engine.recompute_weights()
                ic_engine.save_state_snapshot()

                ic_n_factors = len(new_ic_weights)
                ic_active = sum(1 for w in new_ic_weights.values() if w > 0)

                # Log top factors by IC weight for observability
                if new_ic_weights:
                    top3 = sorted(new_ic_weights.items(), key=lambda x: -x[1])[:3]
                    logger.info("daily_ic_recomputed", extra={
                        "n_factors": ic_n_factors,
                        "n_active": ic_active,
                        "top_factors": {k: round(v, 3) for k, v in top3},
                    })

                # Signal-service runs in a separate process and will pick up
                # new IC weights from Redis via its TTL cache (300s max staleness).

            except Exception as exc:
                logger.warning("daily_ic_recompute_failed", extra={"error": str(exc)[:200]})

            logger.info("daily_loop_complete", extra={
                "verified_decisions": len(verified),
                "factor_weights_count": len(new_fw),
                "category_weights_count": len(new_cw),
                "ic_factors_tracked": ic_n_factors,
                "ic_factors_active": ic_active,
            })

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    await client.post(f"{MEMORY_URL}/memory/consolidate")
            except Exception:
                pass

        except Exception as e:
            logger.warning("daily_loop_failed", extra={"error": str(e)[:200]})

    async def _weekly_loop(self):
        """Every 7 days: meta-learning."""
        try:
            from shared.factors.dynamic_weights import (
                get_recent_accuracy, set_active_protocol,
                load_factor_weights, save_factor_weights,
            )
            from app.core.protocol_router import select_protocol

            accuracy = get_recent_accuracy()

            # Select protocol based on performance
            # Get fear_greed from external data
            fear_greed = 50
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get("{EXTERNAL_DATA_URL}/external/context/BTCUSDT")
                    if resp.status_code == 200:
                        fear_greed = resp.json().get("fear_greed_index", 50)
            except Exception:
                pass

            protocol = select_protocol(accuracy, fear_greed)
            set_active_protocol(protocol["name"])

            # Cull underperforming factors — IC-informed when available
            current_fw = load_factor_weights()
            ic_cull_count = 0
            heuristic_cull_count = 0

            try:
                from shared.factors.ic_weight_engine import get_ic_engine
                ic_engine = get_ic_engine()
                states = ic_engine.get_all_states()

                if states and len(states) >= 5:
                    # IC-based culling: remove factors with |IC| < noise threshold
                    # AND low IC_IR (unstable predictive power)
                    for fname, state_info in states.items():
                        ic = state_info.get("ic", 0)
                        ic_ir = state_info.get("ic_ir", 0)
                        n_obs = state_info.get("n_obs", 0)
                        if n_obs >= 50 and abs(ic) < 0.01 and abs(ic_ir) < 0.2:
                            # Factor has enough data but no predictive power
                            if fname in current_fw:
                                del current_fw[fname]
                                ic_cull_count += 1
                                logger.info("weekly_ic_cull", extra={
                                    "factor": fname, "ic": round(ic, 4),
                                    "ic_ir": round(ic_ir, 3), "n_obs": n_obs,
                                })
                else:
                    # Fallback: simple threshold culling
                    culled = {k: v for k, v in current_fw.items() if v >= 0.15}
                    heuristic_cull_count = len(current_fw) - len(culled)
                    current_fw = culled
            except Exception:
                # Fallback to simple threshold
                culled = {k: v for k, v in current_fw.items() if v >= 0.15}
                heuristic_cull_count = len(current_fw) - len(culled)
                current_fw = culled

            total_culled = ic_cull_count + heuristic_cull_count
            if total_culled > 0:
                save_factor_weights(current_fw)

            logger.info("weekly_loop_complete", extra={
                "accuracy": round(accuracy, 4),
                "protocol": protocol["name"],
                "ic_factors_culled": ic_cull_count,
                "heuristic_factors_culled": heuristic_cull_count,
            })

        except Exception as e:
            logger.warning("weekly_loop_failed", extra={"error": str(e)[:200]})


learning_scheduler = LearningScheduler()
