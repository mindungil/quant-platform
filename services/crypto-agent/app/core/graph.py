"""LangGraph StateGraph for the crypto-agent 8-phase decision loop.

Nodes: gather -> detect -> recall -> select -> score -> check -> execute -> record
Each node accepts AgentState and returns a partial dict merged into state.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from uuid import uuid4

UTC = timezone.utc

import httpx
from langgraph.graph import END, StateGraph

from app.core.config import settings
from app.core.formula_selector import rank_formulas_ml
from app.core.graph_state import AgentState
from app.core.mab_state import formula_mab
from app.models.agent import (
    DecisionRecord,
    MemorySearchRequest,
    MemorySearchResponse,
    SignalSnapshot,
    StrategySnapshot,
)
from shared.formulas import formula_registry
from app.db.repository import decision_repository
from shared.logging import get_logger
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus
from shared.regime import detect_regime, suggest_formula_type

# Ensure formula modules are registered
import shared.formulas.momentum  # noqa: F401
import shared.formulas.reversion  # noqa: F401
import shared.formulas.breakout  # noqa: F401
import shared.formulas.composite  # noqa: F401

logger = get_logger("crypto-agent")


def _clients():
    """Get client instances from engine module (lazy import to avoid circular imports)."""
    import app.core.engine as _engine_mod
    return _engine_mod.signal_client, _engine_mod.memory_client, _engine_mod.strategy_client, _engine_mod.llm_gateway_client, _engine_mod.publisher

SIGNAL_STALENESS_SECONDS = 300


# ---------------------------------------------------------------------------
# Helpers imported lazily from engine to avoid circular imports.
# engine.py -> graph.py (at call time) and graph.py -> engine.py (at call time)
# ---------------------------------------------------------------------------


def _get_engine_helpers():
    """Get engine helper functions (lazy import to avoid circular imports)."""
    import app.core.engine as _engine_mod
    return _engine_mod._build_order_request, _engine_mod._fallback_reasoning, _engine_mod._risk_pre_check


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def gather_node(state: AgentState) -> dict:
    """Phase 1 — Gather: fetch latest signal from signal-service."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    signal_client, _, _, _, _ = _clients()
    signal = signal_client.get_latest_signal(
        state["asset"], user_id=state.get("user_id")
    )
    signal_dict = signal.model_dump(mode="json")

    # Check staleness
    now = datetime.now(UTC)
    feature_ts = signal.feature_timestamp
    if feature_ts.tzinfo is None:
        feature_ts = feature_ts.replace(tzinfo=UTC)
    age = (now - feature_ts).total_seconds()

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["gather"] = duration

    logger.info("graph_gather", extra={
        "asset": state["asset"],
        "signal_score": signal.signal_score,
        "age_seconds": round(age),
    })

    if age > SIGNAL_STALENESS_SECONDS:
        logger.warning("graph_signal_stale", extra={
            "asset": state["asset"], "age": round(age),
        })
        return {
            "signal": signal_dict,
            "signal_age_seconds": age,
            "abort": True,
            "action": "HOLD",
            "threshold_crossed": False,
            "phase_timings": timings,
            "errors": errors,
        }

    return {
        "signal": signal_dict,
        "signal_age_seconds": age,
        "phase_timings": timings,
        "errors": errors,
    }


def detect_node(state: AgentState) -> dict:
    """Phase 2 — Detect: classify market regime from signal features."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    signal = SignalSnapshot.model_validate(state["signal"])

    features = {}
    for field_name in (
        "close", "volume", "rsi_14", "macd", "macd_signal",
        "bb_upper", "bb_lower", "ema_9", "ema_21", "ema_50",
        "sma_20", "atr_14", "adx_14", "stochastic_k", "stochastic_d", "vwap",
    ):
        val = getattr(signal, field_name, None)
        if val is not None:
            features[field_name] = val

    # Fetch external context (non-fatal)
    external_used = False
    try:
        ext_resp = httpx.get(
            f"{settings.external_data_service_base_url}/external/context/{state['asset']}",
            timeout=5.0,
        )
        if ext_resp.status_code == 200:
            ext = ext_resp.json()
            fg = ext.get("fear_greed_index")
            if fg is not None:
                features["fear_greed_index"] = round((fg - 50) / 50, 4)
            if ext.get("news_sentiment") is not None:
                features["news_sentiment"] = ext["news_sentiment"]
            if ext.get("onchain_score") is not None:
                features["onchain_score"] = ext["onchain_score"]
            if ext.get("macro_risk_score") is not None:
                features["macro_risk_score"] = ext["macro_risk_score"]
            # New composite fields
            if ext.get("btc_dominance") is not None:
                features["btc_dominance"] = ext["btc_dominance"]
            comps = ext.get("components", {})
            if comps.get("sentiment_composite") is not None:
                features["sentiment_composite"] = comps["sentiment_composite"]
            if ext.get("volume_score") is not None:
                features["volume_score"] = ext["volume_score"]
            external_used = True
            available_fields = [
                k for k in ("fear_greed_index", "news_sentiment", "onchain_score",
                            "macro_risk_score", "btc_dominance", "volume_score",
                            "price_change_24h", "altcoin_season")
                if ext.get(k) is not None
            ]
            logger.info("graph_detect_external_context", extra={
                "asset": state["asset"],
                "fear_greed": fg,
                "news_sentiment": ext.get("news_sentiment"),
                "available_fields": available_fields,
            })
    except Exception as exc:
        logger.warning("graph_detect_external_skipped", extra={
            "asset": state["asset"], "error": str(exc)[:100],
        })

    try:
        regime = detect_regime(features)
        regime_label = regime.label
        suggested_type = suggest_formula_type(regime)
    except Exception as exc:
        errors.append(f"detect: {exc}")
        regime_label = "unknown"
        suggested_type = "composite_adaptive"

    # Override regime with extreme sentiment labels
    fg_raw = features.get("fear_greed_index")
    fear_greed_raw = ext.get("fear_greed_index") if external_used else None
    if fear_greed_raw is not None:
        if fear_greed_raw < 20 and "_extreme_fear" not in regime_label:
            regime_label = f"{regime_label}_extreme_fear"
            logger.info("graph_detect_extreme_fear", extra={
                "regime": regime_label, "fear_greed_raw": fear_greed_raw,
            })
        elif fear_greed_raw > 80 and "_extreme_greed" not in regime_label:
            regime_label = f"{regime_label}_extreme_greed"
            logger.info("graph_detect_extreme_greed", extra={
                "regime": regime_label, "fear_greed_raw": fear_greed_raw,
            })
    elif fg_raw is not None and fg_raw < -0.6:
        if "fear" not in regime_label:
            regime_label = f"{regime_label}+fear"

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["detect"] = duration

    logger.info("graph_detect", extra={
        "regime": regime_label, "suggested": suggested_type,
        "external_context": external_used,
    })

    return {
        "regime": regime_label,
        "suggested_formula_type": suggested_type,
        "features": features,
        "phase_timings": timings,
        "errors": errors,
    }


def recall_node(state: AgentState) -> dict:
    """Phase 3 — Recall: query memory for formula outcomes, load MAB."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])
    regime_label = state.get("regime") or "unknown"
    asset = state["asset"]
    user_id = state.get("user_id") or "bootstrap"

    formula_scores: dict = {}
    mab_stats: dict = {}

    # 1. Query memory-service for formula outcomes
    try:
        resp = httpx.post(
            f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
            json={"regime_label": regime_label, "asset": asset, "top_k": 20},
            headers={"X-User-ID": user_id},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])

            # 2. Load into MAB
            if items:
                formula_mab.load_from_memory(items)

            # 3. Compute composite scores
            formula_rankings: dict[str, list] = {}
            for item in items:
                record = item.get("record", {})
                fname = record.get("formula_name")
                outcome = record.get("trade_outcome")
                if fname and outcome is not None:
                    formula_rankings.setdefault(fname, []).append(outcome)

            for fname, outcomes in formula_rankings.items():
                n = len(outcomes)
                mean_outcome = sum(outcomes) / n
                sample_confidence = min(math.sqrt(n) / math.sqrt(30), 1.0)
                if n > 1:
                    variance = sum((o - mean_outcome) ** 2 for o in outcomes) / (n - 1)
                    std_dev = math.sqrt(variance) if variance > 0 else 0.001
                    risk_adjusted = mean_outcome / max(std_dev, 0.001)
                else:
                    risk_adjusted = mean_outcome * 10
                composite = risk_adjusted * sample_confidence
                formula_scores[fname] = {
                    "composite": composite,
                    "mean_outcome": mean_outcome,
                    "sample_count": n,
                    "sample_confidence": round(sample_confidence, 3),
                }
    except Exception as exc:
        errors.append(f"recall: {exc}")
        logger.warning("graph_recall_failed", extra={"error": str(exc)})

    # 4. Store MAB stats
    try:
        mab_stats = formula_mab.get_stats()
    except Exception:
        pass

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["recall"] = duration

    logger.info("graph_recall", extra={"formulas_found": len(formula_scores)})

    return {
        "formula_scores": formula_scores,
        "mab_stats": mab_stats,
        "phase_timings": timings,
        "errors": errors,
    }


def select_node(state: AgentState) -> dict:
    """Phase 4 — Select: load active strategy and determine effective user."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    signal = SignalSnapshot.model_validate(state["signal"])
    user_id = state.get("user_id")
    _, _, strategy_client, _, _ = _clients()

    # Primary: load ACTIVE strategy
    strategy = strategy_client.get_active_strategy(
        "crypto",
        user_id=user_id or getattr(signal, "strategy_user_id", None),
    )

    # Check for SHADOW strategies
    is_shadow = False
    try:
        resp = httpx.get(
            f"{settings.strategy_registry_base_url}/strategies/shadow",
            timeout=5.0,
        )
        if resp.status_code == 200:
            shadow_strategies = resp.json()
            for ss in shadow_strategies:
                if ss.get("asset_type") == "crypto":
                    try:
                        shadow_snap = StrategySnapshot.model_validate(ss)
                        strategy = shadow_snap
                        is_shadow = True
                        break
                    except Exception:
                        pass
    except Exception as exc:
        logger.debug("graph_shadow_fetch_failed", extra={"error": str(exc)[:100]})

    strategy_user_id = getattr(signal, "strategy_user_id", None) or strategy.user_id
    effective_user_id = user_id or strategy_user_id or "bootstrap"
    action = signal.direction

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["select"] = duration

    status_label = "SHADOW" if is_shadow else "ACTIVE"
    logger.info("graph_select", extra={
        "strategy": strategy.name, "status": status_label, "action": action,
    })

    return {
        "strategy": strategy.model_dump(mode="json"),
        "effective_user_id": effective_user_id,
        "is_shadow": is_shadow,
        "action": action,
        "threshold_crossed": signal.threshold_crossed,
        "phase_timings": timings,
        "errors": errors,
    }


def score_node(state: AgentState) -> dict:
    """Phase 5 — Score: select and run the best formula."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])
    features = state.get("features") or {}
    formula_scores = state.get("formula_scores") or {}
    suggested_type = state.get("suggested_formula_type") or "composite_adaptive"
    strategy_dict = state.get("strategy") or {}

    selected_formula = None
    selection_method = "default"

    # Priority 1: ML-based selection
    if formula_scores:
        try:
            resp = httpx.post(
                f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
                json={"regime_label": "", "top_k": 200},
                timeout=5.0,
            )
            if resp.status_code == 200:
                all_items = resp.json().get("items", [])
                ml_rankings = rank_formulas_ml(
                    features, all_items, formula_registry.list_names(),
                )
                if ml_rankings:
                    best_ml = ml_rankings[0]
                    candidate = formula_registry.get(best_ml.name)
                    if candidate:
                        selected_formula = candidate
                        selection_method = "ml"
                        logger.info("graph_formula_ml", extra={
                            "formula": best_ml.name,
                            "predicted": best_ml.predicted_score,
                        })
        except Exception as exc:
            logger.warning("graph_ml_failed", extra={"error": str(exc)[:100]})

    # Priority 2: Thompson Sampling MAB
    if selected_formula is None and formula_scores:
        try:
            resp = httpx.post(
                f"{settings.memory_service_base_url}/memory/search/formula-outcomes",
                json={"regime_label": suggested_type, "top_k": 200},
                timeout=5.0,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    formula_mab.load_from_memory(items)
        except Exception:
            pass

        mab_choice = formula_mab.select(
            regime=suggested_type,
            eligible=list(formula_scores.keys()),
        )
        candidate = formula_registry.get(mab_choice)
        if candidate:
            selected_formula = candidate
            selection_method = "mab"
            logger.info("graph_formula_mab", extra={"formula": mab_choice})

    # Priority 3: Memory heuristic
    if selected_formula is None and formula_scores:
        best_name = max(formula_scores, key=lambda f: formula_scores[f]["composite"])
        if formula_scores[best_name]["composite"] > 0:
            candidate = formula_registry.get(best_name)
            if candidate:
                selected_formula = candidate
                selection_method = "memory"

    # Priority 4: Regime default
    if selected_formula is None:
        candidates = formula_registry.get_for_regime(suggested_type)
        if candidates:
            selected_formula = candidates[0]
        else:
            selected_formula = formula_registry.get_default()
        selection_method = "regime_default"

    # Run the formula
    result = selected_formula.compute(features)
    formula_name = selected_formula.name
    formula_score = result.score
    formula_confidence = result.confidence

    # Determine action from formula score vs strategy thresholds
    action = state.get("action", "HOLD")
    threshold_crossed = state.get("threshold_crossed", False)

    if formula_confidence >= 0.3:
        pos_threshold = abs(
            strategy_dict.get("thresholds", {}).get("entry", 0.6)
            if isinstance(strategy_dict.get("thresholds"), dict)
            else 0.6
        )
        if formula_score >= pos_threshold:
            action = "BUY"
            threshold_crossed = True
        elif formula_score <= -pos_threshold:
            action = "SELL"
            threshold_crossed = True
        else:
            action = "HOLD"
            threshold_crossed = False

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["score"] = duration

    logger.info("graph_score", extra={
        "formula": formula_name,
        "score": round(formula_score, 4),
        "confidence": round(formula_confidence, 2),
        "method": selection_method,
        "action": action,
    })

    return {
        "selected_formula": formula_name,
        "formula_score": formula_score,
        "formula_confidence": formula_confidence,
        "action": action,
        "threshold_crossed": threshold_crossed,
        "phase_timings": timings,
        "errors": errors,
    }


def check_node(state: AgentState) -> dict:
    """Phase 6 — Check: pre-flight risk validation."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    signal = SignalSnapshot.model_validate(state["signal"])
    strategy = StrategySnapshot.model_validate(state["strategy"])
    asset = state["asset"]
    action = state.get("action", "HOLD")

    _, _, _risk_pre_check = _get_engine_helpers()
    issues = _risk_pre_check(strategy, signal, asset, action)
    risk_issues = list(issues)

    # If stale or duplicate issues, force HOLD
    has_blocking = any(
        "stale" in issue.lower() or "duplicate" in issue.lower()
        for issue in risk_issues
    )

    result_action = action
    result_threshold = state.get("threshold_crossed", False)
    if has_blocking:
        result_action = "HOLD"
        result_threshold = False
        logger.warning("graph_check_blocked", extra={
            "asset": asset, "issues": risk_issues,
        })
    elif risk_issues:
        logger.warning("graph_check_warnings", extra={
            "asset": asset, "issues": risk_issues,
        })

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["check"] = duration

    return {
        "risk_issues": risk_issues,
        "action": result_action,
        "threshold_crossed": result_threshold,
        "phase_timings": timings,
        "errors": errors,
    }


def execute_node(state: AgentState) -> dict:
    """Phase 7 — Execute: generate reasoning, build decision, publish action."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    _, memory_client, _, llm_gateway_client, publisher = _clients()

    signal = SignalSnapshot.model_validate(state["signal"])
    strategy = StrategySnapshot.model_validate(state["strategy"])
    asset = state["asset"]
    action = state.get("action", "HOLD")
    effective_user_id = state.get("effective_user_id", "bootstrap")
    correlation_id = state.get("correlation_id")
    is_shadow = state.get("is_shadow", False)
    formula_name = state.get("selected_formula", "unknown")
    formula_confidence = state.get("formula_confidence", 0.0)
    formula_score = state.get("formula_score", 0.0)
    regime_label = state.get("regime", "unknown")
    threshold_crossed = state.get("threshold_crossed", False)

    # Retrieve memory for reasoning context
    try:
        memory_response = memory_client.search(
            MemorySearchRequest(
                user_id=effective_user_id,
                asset=asset,
                signal_score=signal.signal_score,
                action=signal.direction,
                strategy_id=strategy.id,
            )
        )
    except Exception:
        memory_response = MemorySearchResponse(
            query=MemorySearchRequest(
                user_id=effective_user_id,
                asset=asset,
                signal_score=signal.signal_score,
            ),
            items=[],
        )

    # LLM reasoning with fallback
    _build_order_request, _fallback_reasoning, _ = _get_engine_helpers()
    reasoning = _fallback_reasoning(
        asset, strategy.name, formula_score,
        len(memory_response.items), signal.components,
    )
    try:
        reasoning = llm_gateway_client.generate_reasoning(
            asset=asset,
            signal_score=formula_score,
            strategy_name=strategy.name,
            memory_count=len(memory_response.items),
            components=signal.components,
        )
    except Exception as exc:
        logger.warning("graph_llm_fallback", extra={"error": str(exc)[:100]})

    # Prefix with formula/regime info
    reasoning = (
        f"[formula={formula_name} regime={regime_label}"
        f"{' SHADOW' if is_shadow else ''}] {reasoning}"
    )

    # Build decision record
    components = dict(signal.components)
    components["formula_confidence"] = round(formula_confidence, 4)

    decision_id = str(uuid4())
    decision = DecisionRecord(
        decision_id=decision_id,
        timestamp=datetime.now(UTC),
        user_id=effective_user_id,
        asset=asset,
        asset_type="crypto",
        signal_score=formula_score,
        strategy_id=strategy.id,
        strategy_name=strategy.name,
        action=action,
        threshold_crossed=threshold_crossed,
        reasoning=reasoning,
        memory_refs=[item.record.id for item in memory_response.items if item.record.id],
        components=components,
        correlation_id=correlation_id or decision_id,
        reference_price=getattr(signal, "reference_price", None),
    )

    # Publish execution action if threshold crossed
    order_submitted = False
    order_request = None
    if decision.threshold_crossed and decision.action in {"BUY", "SELL"}:
        order_request = _build_order_request(decision, shadow_override=is_shadow)
        if order_request is not None:
            try:
                publisher.publish_agent_action(decision, order_request)
                order_submitted = True
            except Exception as exc:
                errors.append(f"execute_publish: {exc}")
                logger.warning("graph_publish_failed", extra={"error": str(exc)[:100]})

    if not order_submitted and action != "HOLD":
        logger.warning("graph_order_not_submitted", extra={
            "asset": asset, "action": action,
        })

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["execute"] = duration

    return {
        "decision_id": decision_id,
        "order_request": order_request,
        "order_submitted": order_submitted,
        "reasoning": reasoning,
        "action": action,
        "threshold_crossed": threshold_crossed,
        "phase_timings": timings,
        "errors": errors,
    }


def record_node(state: AgentState) -> dict:
    """Phase 8 — Record: persist decision to DB, memory, and realtime bus."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    _, memory_client, _, _, _ = _clients()
    # Reconstruct decision from state for persistence
    signal = SignalSnapshot.model_validate(state["signal"])
    strategy_dict = state.get("strategy") or {}
    asset = state["asset"]
    action = state.get("action", "HOLD")
    effective_user_id = state.get("effective_user_id", "bootstrap")
    decision_id = state.get("decision_id") or str(uuid4())
    reasoning = state.get("reasoning") or ""
    formula_score = state.get("formula_score", 0.0)
    threshold_crossed = state.get("threshold_crossed", False)
    correlation_id = state.get("correlation_id") or decision_id
    formula_name = state.get("selected_formula", "unknown")
    formula_confidence = state.get("formula_confidence", 0.0)
    regime_label = state.get("regime", "unknown")
    is_shadow = state.get("is_shadow", False)

    components = dict(signal.components)
    components["formula_confidence"] = round(formula_confidence, 4)

    decision = DecisionRecord(
        decision_id=decision_id,
        timestamp=datetime.now(UTC),
        user_id=effective_user_id,
        asset=asset,
        asset_type="crypto",
        signal_score=formula_score,
        strategy_id=strategy_dict.get("id", "unknown"),
        strategy_name=strategy_dict.get("name", "unknown"),
        action=action,
        threshold_crossed=threshold_crossed,
        reasoning=reasoning,
        memory_refs=[],
        components=components,
        correlation_id=correlation_id,
        reference_price=signal.reference_price,
    )

    try:
        decision_repository.save(asset, decision)
        memory_client.record(decision.to_memory_record())
    except Exception as exc:
        errors.append(f"record_persist: {exc}")
        logger.warning("graph_record_persist_failed", extra={"error": str(exc)[:100]})

    try:
        realtime_bus = RealtimeBus(RedisStore(settings.redis_url))
        realtime_bus.publish(
            event_type="agent.decision",
            source="crypto-agent",
            user_id=decision.user_id,
            correlation_id=decision.correlation_id,
            data={
                "decision_id": decision.decision_id,
                "asset": decision.asset,
                "asset_type": decision.asset_type,
                "action": decision.action,
                "signal_score": decision.signal_score,
                "strategy_id": decision.strategy_id,
                "strategy_name": decision.strategy_name,
                "reasoning": decision.reasoning,
                "threshold_crossed": decision.threshold_crossed,
                "timestamp": decision.timestamp.isoformat(),
                "reference_price": decision.reference_price,
                "phase_timings": state.get("phase_timings", {}),
            },
        )
    except Exception as exc:
        errors.append(f"record_realtime: {exc}")
        logger.debug("graph_realtime_failed", extra={"error": str(exc)[:100]})

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["record"] = duration

    logger.info("graph_record", extra={
        "decision_id": decision_id, "asset": asset, "action": action,
    })

    return {
        "recorded": True,
        "phase_timings": timings,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_agent_graph() -> StateGraph:
    workflow = StateGraph(AgentState)
    workflow.add_node("gather", gather_node)
    workflow.add_node("detect", detect_node)
    workflow.add_node("recall", recall_node)
    workflow.add_node("select", select_node)
    workflow.add_node("score", score_node)
    workflow.add_node("check", check_node)
    workflow.add_node("execute", execute_node)
    workflow.add_node("record", record_node)

    workflow.set_entry_point("gather")

    # Linear flow with conditional abort after gather (stale signal)
    workflow.add_conditional_edges(
        "gather",
        lambda s: "abort" if s.get("abort") else "detect",
        {"abort": END, "detect": "detect"},
    )
    workflow.add_edge("detect", "recall")
    workflow.add_edge("recall", "select")
    workflow.add_edge("select", "score")
    # After score: if HOLD with no threshold → skip to record
    workflow.add_conditional_edges(
        "score",
        lambda s: "abort" if s.get("action") == "HOLD" and not s.get("threshold_crossed") else "check",
        {"abort": "record", "check": "check"},
    )
    workflow.add_edge("check", "execute")
    workflow.add_edge("execute", "record")
    workflow.add_edge("record", END)

    return workflow.compile()


agent_graph = build_agent_graph()
