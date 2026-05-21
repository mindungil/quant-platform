"""Outcome Reinforcement Consumer — 주문 체결 후 메모리 자동 업데이트.

NATS에서 order.filled 이벤트를 수신하면:
1. 해당 decision의 memory_refs를 조회
2. PnL 기반 trade_outcome 계산
3. 각 메모리 레코드에 결과 반영 (reinforce)

이를 통해 에이전트가 실시간으로 자신의 판단 결과를 학습.
"""
from __future__ import annotations

import logging

import os
from app.core.config import settings
try:
    from app.core.mab_state import formula_mab
except ImportError:
    formula_mab = None  # public-only build (bandit / mab_state are private IP)
from app.core.tca import compute_tca_reward

# TCA cost weight — bps-for-bps penalty by default. Tune up to make the
# bandit more slippage-averse, down (or 0) to disable the correction.
TCA_COST_WEIGHT = float(os.getenv("TCA_COST_WEIGHT", "1.0"))
from app.db.repository import decision_repository
from app.services.memory_client import MemoryClient
from shared.events import JetStreamBus
from shared.persistence import RedisStore
from prometheus_client import Counter, Gauge

logger = logging.getLogger("outcome-consumer")

outcome_reinforcement_total = Counter(
    "outcome_reinforcement_total",
    "Total outcome reinforcement attempts",
    ["status"],
)
outcome_reinforcement_skipped_total = Counter(
    "outcome_reinforcement_skipped_total",
    "Total outcome reinforcements skipped",
)
outcome_reinforcement_pnl_total = Gauge(
    "outcome_reinforcement_pnl_total",
    "Cumulative PnL from reinforced outcomes",
)
# F3: visibility into the D20 wiring — how often does a realized order fill
# actually translate into a MAB.update call? Labeled by status so we can tell
# the difference between "wired correctly" and "silently dropped at every
# guard along the way".
mab_outcome_update_total = Counter(
    "mab_outcome_update_total",
    "MAB updates triggered by realized outcomes",
    ["status"],  # success | no_formula | mab_unavailable | exception
)
mab_outcome_update_reward = Gauge(
    "mab_outcome_update_reward_last",
    "Last reward value passed to formula_mab.update from outcome consumer",
)

memory_client = MemoryClient(settings.memory_service_base_url)


class OutcomeReinforcementConsumer:
    def __init__(self) -> None:
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )

    async def start(self) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(
            settings.execution_jetstream_stream,
            ["order.filled", "order.filled.dlq", "memory.reinforce.failed"],
        )
        await self._bus.subscribe(
            stream=settings.execution_jetstream_stream,
            subject="order.filled",
            durable="outcome-reinforcement-consumer",
            callback=self._handle,
            dlq_subject="order.filled.dlq",
        )
        logger.info("outcome_reinforcement_consumer_started")

    async def stop(self) -> None:
        await self._bus.close()

    async def _handle(self, payload: dict) -> None:
        """Process order.filled event → reinforce memory."""
        try:
            data = payload.get("data", {})
            correlation_id = data.get("correlation_id") or payload.get("correlation_id")
            order_id = data.get("order_id", "")
            asset = data.get("asset", "")
            side = data.get("side", "")
            # D20: the order.filled NATS event carries OrderResponse.model_dump()
            # which nests fill data under `fill`. fall back to top-level keys
            # for older event shapes.
            fill = data.get("fill") or {}
            fill_price = float(
                fill.get("filled_price")
                or data.get("fill_price", 0)
                or data.get("price", 0)
            )
            quantity = float(
                fill.get("filled_quantity")
                or data.get("quantity", 0)
            )
            # D20: pnl now flows from the shadow recorder via FillSnapshot.pnl.
            pnl = float(
                fill.get("pnl")
                or data.get("pnl", 0)
            )

            if not correlation_id:
                logger.debug("order_filled_no_correlation", extra={"order_id": order_id})
                outcome_reinforcement_skipped_total.inc()
                return

            # Find the decision that created this order
            decision = decision_repository.get_by_correlation_id(correlation_id)
            if not decision:
                logger.debug("order_filled_no_decision", extra={"correlation_id": correlation_id})
                outcome_reinforcement_skipped_total.inc()
                return

            decision_data = decision.get("payload", decision)
            memory_refs = decision_data.get("memory_refs", [])
            reference_price = float(decision_data.get("reference_price", 0))
            signal_score = float(decision_data.get("signal_score", 0))

            # F1c: TCA expects `pnl` as a fractional return (e.g. 0.012 = +1.2%),
            # but the shadow recorder emits raw-dollar PnL. On testnet notional
            # of ~$0.1, that produced ~1e-5-magnitude rewards — three orders of
            # magnitude smaller than hindsight, so the MAB barely learned from
            # realized outcomes. Normalize by notional here so realized and
            # hindsight rewards live on the same scale.
            notional = abs(fill_price * quantity) if (fill_price and quantity) else 0.0
            pnl_fraction = pnl / notional if notional > 0 else pnl
            tca = compute_tca_reward(
                pnl=pnl_fraction,
                fill_price=fill_price,
                reference_price=reference_price,
                side=side,
                tca_cost_weight=TCA_COST_WEIGHT,
            )
            trade_outcome = tca.tca_adjusted_reward

            # Simple Sharpe-like metric: outcome / abs(signal) as efficiency
            outcome_sharpe = trade_outcome / max(abs(signal_score), 0.01)

            # Reinforce each linked memory record
            reinforced = 0
            for mem_id in memory_refs:
                success = False
                for attempt in range(1, 4):  # 3 attempts
                    try:
                        memory_client.reinforce(mem_id, trade_outcome, outcome_sharpe)
                        reinforced += 1
                        success = True
                        break
                    except Exception as exc:
                        if attempt < 3:
                            import time
                            time.sleep(1)
                        else:
                            logger.warning("reinforce_failed_after_retries", extra={
                                "memory_id": mem_id, "error": str(exc)[:100], "attempts": 3,
                            })
                if not success:
                    outcome_reinforcement_total.labels(status="failed").inc()

            # Also reinforce the decision's own memory record if it has one
            decision_memory_id = decision_data.get("decision_id")
            if decision_memory_id and decision_memory_id not in memory_refs:
                try:
                    memory_client.reinforce(decision_memory_id, trade_outcome, outcome_sharpe)
                    reinforced += 1
                except Exception as exc:
                    logger.warning("reinforce_decision_memory_failed", extra={
                        "memory_id": decision_memory_id,
                        "error": str(exc)[:100],
                        "correlation_id": correlation_id,
                    })
                    # Publish failure event for observability
                    try:
                        await self._bus.publish(
                            "memory.reinforce.failed",
                            __import__("shared.events", fromlist=["EventEnvelope"]).EventEnvelope(
                                event_type="memory.reinforce.failed",
                                source="outcome-consumer",
                                correlation_id=correlation_id,
                                data={
                                    "memory_id": decision_memory_id,
                                    "error": str(exc)[:100],
                                    "correlation_id": correlation_id,
                                },
                            ),
                        )
                    except Exception:
                        logger.debug("reinforce_failed_event_publish_error")

            # Update MAB with trade outcome for formula learning
            components = decision_data.get("components", {})
            # D20: graph.py writes the formula key as "style_formula" and the
            # regime key as "regime". Older code paths used "formula_name" /
            # "regime_label" — accept both so the MAB receives outcomes from
            # whichever publisher produced the decision.
            formula_name = (
                decision_data.get("formula_name")
                or components.get("formula_name")
                or components.get("style_formula")
            )
            regime_label = (
                decision_data.get("regime_label")
                or components.get("regime_label")
                or components.get("regime")
            )
            # Extract formula name from reasoning prefix if not in components
            reasoning = decision_data.get("reasoning", "")
            if not formula_name and reasoning.startswith("[formula="):
                try:
                    formula_name = reasoning.split("formula=")[1].split(" ")[0].split("]")[0]
                except (IndexError, ValueError):
                    pass
            if not regime_label and "regime=" in reasoning:
                try:
                    regime_label = reasoning.split("regime=")[1].split(" ")[0].split("]")[0]
                except (IndexError, ValueError):
                    pass

            if formula_name and formula_mab is not None:
                try:
                    # D20: graph.py stores the display name "ensemble+<arm>".
                    # MAB arms are registered with the raw name (e.g. "macd_histogram").
                    # Strip the prefix so the right arm receives the reward;
                    # without this every update is silently dropped.
                    mab_arm = formula_name
                    if isinstance(mab_arm, str) and mab_arm.startswith("ensemble+"):
                        mab_arm = mab_arm[len("ensemble+"):]
                    formula_mab.update(mab_arm, trade_outcome, regime=regime_label)
                    mab_outcome_update_total.labels(status="success").inc()
                    mab_outcome_update_reward.set(trade_outcome)
                    logger.info("mab_updated_from_outcome", extra={
                        "formula_name": formula_name,
                        "regime": regime_label,
                        "trade_outcome": f"{trade_outcome:.4f}",
                        "realized_slippage_bp": f"{tca.realized_slippage_bp:.2f}",
                        "tca_cost_bp": f"{tca.tca_cost_bp:.2f}",
                        "reward_source": tca.reward_source,
                    })
                except Exception as exc:
                    mab_outcome_update_total.labels(status="exception").inc()
                    logger.warning("mab_update_failed", extra={
                        "formula_name": formula_name, "error": str(exc)[:100],
                    })
            elif not formula_name:
                mab_outcome_update_total.labels(status="no_formula").inc()
            else:
                mab_outcome_update_total.labels(status="mab_unavailable").inc()

            # ── D9: V3 maker_taker bandit production wire ──
            # Every fill feeds the per-context bandit. Context = (spread,
            # vol_regime, order_size, urgency). Reward = -realized_slippage / 100.
            # First cycles → bandit posteriors start populated; subsequent
            # cycles → maker/taker decision quality measurable in Grafana.
            try:
                from shared.execution.maker_taker_bandit import (
                    context_key, slippage_to_reward, MakerTakerBandit,
                )
                # Lazy module-level singleton (mirrors mab_state.py pattern)
                global _MT_BANDIT  # type: ignore
                try:
                    _MT_BANDIT  # type: ignore[used-before-assignment]
                except NameError:
                    _MT_BANDIT = MakerTakerBandit(
                        epsilon=float(os.getenv("MAKER_TAKER_EPSILON", "0.15")),
                    )
                # Bucket size by USD notional from fill price × quantity
                qty = float(data.get("filled_quantity") or data.get("quantity") or 0)
                size_usd = abs(qty * fill_price) if fill_price else 0.0
                # Default conservative regime values when missing — bandit
                # learns the right action per bucket regardless.
                ctx = context_key(
                    spread_bp=float(data.get("spread_bp", 10.0)),
                    annualized_vol=float(data.get("annualized_vol", 0.30)),
                    order_size_usd=size_usd,
                    urgency=str(data.get("urgency", "normal")),
                )
                # If order_type label present, use it; else fall back to TAKER
                # (most paper-fills are simulated market orders).
                action = str(data.get("order_type", "TAKER")).upper()
                if action not in ("MAKER", "TAKER"):
                    action = "TAKER"
                reward = slippage_to_reward(tca.realized_slippage_bp)
                _MT_BANDIT.update(ctx, action, reward)

                # V12: emit the maker/taker decision metric directly. Until
                # MakerTakerBandit.select() is wired at the order-placement
                # layer, the canonical decision counter
                # quant_v3_maker_taker_decisions_total was structurally
                # always 0 (V6). Recording the actual executed action here
                # closes that gap and also populates the realized-slippage
                # histogram that V3HighRealizedSlippage depends on.
                try:
                    from shared.observability_v3 import record_maker_taker_decision
                    record_maker_taker_decision(
                        ctx, action,
                        realized_slippage_bp=tca.realized_slippage_bp,
                    )
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("maker_taker_bandit_update_skipped: %s", str(exc)[:120])

            # ── IC Engine: feed real fill outcome for factor weight learning ──
            # This is the highest-quality signal: actual trade result, not hindsight.
            try:
                import math
                from shared.factors.ic_weight_engine import get_ic_engine

                _IC_META_KEYS = frozenset({
                    "ensemble_score", "style_score", "style_formula",
                    "formula_confidence", "regime", "adx_filter",
                    "_n_components", "_insufficient_data", "_agreement_bonus",
                    "_weight_mode",
                })
                factor_scores = {
                    k: float(v) for k, v in components.items()
                    if isinstance(v, (int, float))
                    and math.isfinite(float(v))
                    and not k.startswith("cat_")
                    and k not in _IC_META_KEYS
                }
                if factor_scores and math.isfinite(trade_outcome):
                    regime_label = components.get("regime") if isinstance(components.get("regime"), str) else None
                    get_ic_engine().update(factor_scores, trade_outcome, regime=regime_label)
                    logger.debug("ic_engine_updated_from_fill", extra={
                        "n_factors": len(factor_scores),
                        "regime": regime_label,
                        "trade_outcome": f"{trade_outcome:.4f}",
                    })

                    # Phase O: feed live fill outcome into the per-regime
                    # Kelly persistence. Incremental — grab whatever's
                    # stored, add this observation, write back.
                    if regime_label:
                        try:
                            from shared.portfolio.kelly_store import KellyStore
                            store = KellyStore()
                            snap = store.read()
                            if snap is None:
                                fractions = {regime_label: 0.0}
                                samples = {regime_label: 0}
                            else:
                                fractions = dict(snap.fractions)
                                samples = dict(snap.samples)
                            # Incremental mean of trade_outcome as edge
                            # proxy; combined with combine() at scoring
                            # time via KellyStore.blend().
                            n = samples.get(regime_label, 0)
                            prev = fractions.get(regime_label, 0.0)
                            samples[regime_label] = n + 1
                            # Simple running mean of outcome bounded [0, 1]
                            # — MetaSignalEngine interprets this as an
                            # adjustment factor vs. local estimate.
                            new_edge = prev * (n / (n + 1)) + max(trade_outcome, 0.0) / (n + 1)
                            fractions[regime_label] = float(max(0.0, min(new_edge, 0.5)))
                            store.write(fractions, samples)
                        except Exception as exc:
                            logger.debug("kelly_store_update_failed", extra={"error": str(exc)[:80]})
            except Exception as exc:
                logger.debug("ic_engine_fill_update_failed", extra={"error": str(exc)[:100]})

            logger.info("outcome_reinforced", extra={
                "correlation_id": correlation_id,
                "asset": asset,
                "trade_outcome": f"{trade_outcome:.4f}",
                "reinforced_count": reinforced,
            })
            outcome_reinforcement_total.labels(status="success").inc()
            outcome_reinforcement_pnl_total.inc(trade_outcome)

        except Exception as exc:
            logger.error("outcome_handle_error", extra={"error": str(exc)[:200]})


outcome_consumer = OutcomeReinforcementConsumer()
