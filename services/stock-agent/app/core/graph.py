"""LangGraph StateGraph for the stock-agent 8-phase decision loop.

Nodes: gather -> detect -> recall -> select -> score -> check -> execute -> record
Each node accepts AgentState and returns a partial dict merged into state.
"""
from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from langgraph.graph import END, StateGraph
from prometheus_client import Counter

from app.core.graph_state import AgentState
from app.models.agent import DecisionRecord, SignalSnapshot, StrategySnapshot
from shared.formulas import formula_registry
from shared.logging import get_logger
from shared.regime import detect_regime, suggest_formula_type

# Ensure formula modules are registered
import shared.formulas.momentum   # noqa: F401
import shared.formulas.reversion  # noqa: F401
import shared.formulas.breakout   # noqa: F401
import shared.formulas.composite  # noqa: F401

UTC = timezone.utc
logger = get_logger("stock-agent")

# ---------------------------------------------------------------------------
# Service URLs
# ---------------------------------------------------------------------------
SIGNAL_SERVICE_BASE_URL = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
MEMORY_SERVICE_BASE_URL = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
STRATEGY_REGISTRY_BASE_URL = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
ORDER_SERVICE_BASE_URL = os.getenv("ORDER_SERVICE_BASE_URL", "http://localhost:8011")
RISK_SERVICE_BASE_URL = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8012")
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "system")

SIGNAL_STALENESS_SECONDS = 300

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
stock_agent_decisions_total = Counter(
    "stock_agent_decisions_total",
    "Total stock agent decisions",
    ["asset", "action"],
)
stock_agent_phase_total = Counter(
    "stock_agent_phase_total",
    "Total stock agent phase executions",
    ["phase", "status"],
)


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def gather_node(state: AgentState) -> dict:
    """Phase 1 — Gather: fetch latest signal from signal-service."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])
    asset = state["asset"]
    user_id = state.get("user_id")
    if user_id is None:
        logger.warning("no_user_id_in_request", extra={"asset": asset})
        user_id = DEFAULT_USER_ID

    try:
        headers = {"X-User-ID": user_id}
        resp = httpx.get(
            f"{SIGNAL_SERVICE_BASE_URL}/signals/{asset}/latest",
            headers=headers,
            timeout=5.0,
        )
        resp.raise_for_status()
        signal_data = resp.json()
    except Exception as exc:
        errors.append(f"gather: {exc}")
        logger.warning("graph_gather_failed", extra={"asset": asset, "error": str(exc)[:200]})
        stock_agent_phase_total.labels(phase="gather", status="error").inc()
        return {
            "signal": None,
            "signal_age_seconds": None,
            "abort": True,
            "action": "HOLD",
            "threshold_crossed": False,
            "phase_timings": {"gather": round((time.monotonic() - t0) * 1000, 2)},
            "errors": errors,
        }

    signal = SignalSnapshot(
        asset=asset,
        signal_score=float(signal_data.get("signal_score", 0.0)),
        threshold=float(signal_data.get("threshold", 0.4)),
        threshold_crossed=signal_data.get("threshold_crossed", False),
        direction=signal_data.get("direction", "HOLD"),
        components=signal_data.get("components", {}),
        feature_timestamp=signal_data.get("feature_timestamp", datetime.now(UTC).isoformat()),
        reference_price=signal_data.get("reference_price"),
        strategy_id=signal_data.get("strategy_id"),
        strategy_user_id=signal_data.get("strategy_user_id"),
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
        "asset": asset, "signal_score": signal.signal_score, "age_seconds": round(age),
    })

    if age > SIGNAL_STALENESS_SECONDS:
        logger.warning("graph_signal_stale", extra={"asset": asset, "age": round(age)})
        stock_agent_phase_total.labels(phase="gather", status="stale").inc()
        return {
            "signal": signal_dict,
            "signal_age_seconds": age,
            "abort": True,
            "action": "HOLD",
            "threshold_crossed": False,
            "phase_timings": timings,
            "errors": errors,
        }

    stock_agent_phase_total.labels(phase="gather", status="ok").inc()
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

    features = {"asset": state.get("asset", "default")}
    for field_name in (
        "close", "volume", "rsi_14", "macd", "macd_signal",
        "bb_upper", "bb_lower", "ema_9", "ema_21", "ema_50",
        "sma_20", "atr_14", "adx_14", "stochastic_k", "stochastic_d", "vwap",
    ):
        val = signal.components.get(field_name)
        if val is not None:
            features[field_name] = val

    try:
        regime = detect_regime(features, asset=state.get("asset"))
        regime_label = regime.label
        suggested_type = suggest_formula_type(regime)
    except Exception as exc:
        errors.append(f"detect: {exc}")
        regime_label = "unknown"
        suggested_type = "composite_adaptive"

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["detect"] = duration

    logger.info("graph_detect", extra={"regime": regime_label, "suggested": suggested_type})
    stock_agent_phase_total.labels(phase="detect", status="ok").inc()

    return {
        "regime": regime_label,
        "suggested_formula_type": suggested_type,
        "features": features,
        "phase_timings": timings,
        "errors": errors,
    }


def recall_node(state: AgentState) -> dict:
    """Phase 3 — Recall: query memory-service for relevant past decisions."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])
    regime_label = state.get("regime") or "unknown"
    asset = state["asset"]
    user_id = state.get("user_id")
    if user_id is None:
        logger.warning("no_user_id_in_request", extra={"asset": asset})
        user_id = DEFAULT_USER_ID

    formula_scores: dict = {}

    try:
        resp = httpx.post(
            f"{MEMORY_SERVICE_BASE_URL}/memory/search/formula-outcomes",
            json={"regime_label": regime_label, "asset": asset, "top_k": 20},
            headers={"X-User-ID": user_id},
            timeout=5.0,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
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
                    risk_adjusted = mean_outcome / max(math.sqrt(max(variance, 0)), 0.001)
                else:
                    risk_adjusted = mean_outcome * 10
                formula_scores[fname] = {
                    "composite": risk_adjusted * sample_confidence,
                    "mean_outcome": mean_outcome,
                    "sample_count": n,
                    "sample_confidence": round(sample_confidence, 3),
                }
    except Exception as exc:
        errors.append(f"recall: {exc}")
        logger.warning("graph_recall_failed", extra={"error": str(exc)[:200]})

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["recall"] = duration

    logger.info("graph_recall", extra={"formulas_found": len(formula_scores)})
    stock_agent_phase_total.labels(phase="recall", status="ok").inc()

    return {
        "formula_scores": formula_scores,
        "phase_timings": timings,
        "errors": errors,
    }


def select_node(state: AgentState) -> dict:
    """Phase 4 — Select: load active strategy from strategy-registry."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])
    signal = SignalSnapshot.model_validate(state["signal"])
    user_id = state.get("user_id")

    strategy_dict = None
    try:
        params = {"asset_type": "stock", "status": "ACTIVE"}
        if user_id:
            params["user_id"] = user_id
        resp = httpx.get(
            f"{STRATEGY_REGISTRY_BASE_URL}/strategies",
            params=params,
            timeout=5.0,
        )
        if resp.status_code == 200:
            strategies = resp.json()
            if isinstance(strategies, list) and strategies:
                strategy_dict = strategies[0]
            elif isinstance(strategies, dict) and strategies.get("id"):
                strategy_dict = strategies
    except Exception as exc:
        errors.append(f"select: {exc}")
        logger.warning("graph_select_failed", extra={"error": str(exc)[:200]})

    if strategy_dict is None:
        strategy_dict = {
            "id": "default-stock",
            "user_id": user_id or "bootstrap",
            "name": "default-stock-strategy",
            "asset_type": "stock",
            "indicators": ["rsi_14", "macd", "ema_9", "ema_21"],
            "weights": {},
            "thresholds": {"entry": 0.6},
            "version": "1",
            "status": "ACTIVE",
        }

    strategy = StrategySnapshot.model_validate(strategy_dict)
    effective_user_id = user_id or strategy.user_id or DEFAULT_USER_ID
    action = signal.direction

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["select"] = duration

    logger.info("graph_select", extra={"strategy": strategy.name, "action": action})
    stock_agent_phase_total.labels(phase="select", status="ok").inc()

    return {
        "strategy": strategy.model_dump(mode="json"),
        "effective_user_id": effective_user_id,
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

    # Priority 1: Memory heuristic (best composite score)
    if formula_scores:
        best_name = max(formula_scores, key=lambda f: formula_scores[f]["composite"])
        if formula_scores[best_name]["composite"] > 0:
            candidate = formula_registry.get(best_name)
            if candidate:
                selected_formula = candidate

    # Priority 2: Regime default
    if selected_formula is None:
        candidates = formula_registry.get_for_regime(suggested_type)
        if candidates:
            selected_formula = candidates[0]
        else:
            selected_formula = formula_registry.get_default()

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
        "action": action,
    })
    stock_agent_phase_total.labels(phase="score", status="ok").inc()

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
    """Phase 6 — Check: POST to risk-service for pre-flight validation."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])
    asset = state["asset"]
    action = state.get("action", "HOLD")
    user_id = state.get("effective_user_id") or DEFAULT_USER_ID

    risk_issues: list[str] = []

    try:
        payload = {
            "asset": asset,
            "asset_type": "stock",
            "action": action,
            "signal_score": state.get("formula_score", 0.0),
            "strategy_id": (state.get("strategy") or {}).get("id", "unknown"),
            "user_id": user_id,
        }
        resp = httpx.post(
            f"{RISK_SERVICE_BASE_URL}/risk/check",
            json=payload,
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            risk_issues = data.get("issues", [])
        else:
            logger.warning("graph_check_risk_non200", extra={
                "status": resp.status_code, "asset": asset,
            })
    except Exception as exc:
        errors.append(f"check: {exc}")
        logger.warning("graph_check_failed", extra={"error": str(exc)[:200]})

    has_blocking = any(
        "stale" in issue.lower() or "duplicate" in issue.lower() or "rejected" in issue.lower()
        for issue in risk_issues
    )

    result_action = action
    result_threshold = state.get("threshold_crossed", False)
    if has_blocking:
        result_action = "HOLD"
        result_threshold = False
        logger.warning("graph_check_blocked", extra={"asset": asset, "issues": risk_issues})

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["check"] = duration
    stock_agent_phase_total.labels(phase="check", status="ok").inc()

    return {
        "risk_issues": risk_issues,
        "action": result_action,
        "threshold_crossed": result_threshold,
        "phase_timings": timings,
        "errors": errors,
    }


def execute_node(state: AgentState) -> dict:
    """Phase 7 — Execute: if risk approved and action != HOLD, POST order to order-service."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    signal = SignalSnapshot.model_validate(state["signal"])
    strategy_dict = state.get("strategy") or {}
    asset = state["asset"]
    action = state.get("action", "HOLD")
    effective_user_id = state.get("effective_user_id") or DEFAULT_USER_ID
    correlation_id = state.get("correlation_id")
    formula_name = state.get("selected_formula", "unknown")
    formula_score = state.get("formula_score", 0.0)
    formula_confidence = state.get("formula_confidence", 0.0)
    regime_label = state.get("regime", "unknown")
    threshold_crossed = state.get("threshold_crossed", False)

    decision_id = str(uuid4())
    reasoning = (
        f"[formula={formula_name} regime={regime_label}] "
        f"{asset} score={formula_score:.4f} confidence={formula_confidence:.2f} → {action}"
    )

    components = dict(signal.components)
    components["formula_confidence"] = round(formula_confidence, 4)

    order_submitted = False
    order_request = None

    if threshold_crossed and action in ("BUY", "SELL"):
        order_request = {
            "asset": asset,
            "asset_type": "stock",
            "side": action,
            "exchange": "alpaca",
            "user_id": effective_user_id,
            "strategy_id": strategy_dict.get("id", "unknown"),
            "decision_id": decision_id,
            "signal_score": formula_score,
            "reference_price": signal.reference_price,
            "correlation_id": correlation_id or decision_id,
        }
        try:
            resp = httpx.post(
                f"{ORDER_SERVICE_BASE_URL}/orders",
                json=order_request,
                timeout=10.0,
            )
            if resp.status_code in (200, 201, 202):
                order_submitted = True
                logger.info("graph_order_submitted", extra={
                    "asset": asset, "action": action, "decision_id": decision_id,
                })
            else:
                errors.append(f"execute_order: status={resp.status_code}")
                logger.warning("graph_order_rejected", extra={
                    "asset": asset, "status": resp.status_code,
                })
        except Exception as exc:
            errors.append(f"execute_order: {exc}")
            logger.warning("graph_order_failed", extra={"error": str(exc)[:200]})

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["execute"] = duration
    stock_agent_phase_total.labels(phase="execute", status="ok").inc()

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
    """Phase 8 — Record: persist decision to memory-service."""
    t0 = time.monotonic()
    errors = list(state.get("errors") or [])

    signal_dict = state.get("signal") or {}
    strategy_dict = state.get("strategy") or {}
    asset = state["asset"]
    action = state.get("action", "HOLD")
    effective_user_id = state.get("effective_user_id") or DEFAULT_USER_ID
    decision_id = state.get("decision_id") or str(uuid4())
    reasoning = state.get("reasoning") or ""
    formula_score = state.get("formula_score", 0.0)
    threshold_crossed = state.get("threshold_crossed", False)
    correlation_id = state.get("correlation_id") or decision_id
    formula_name = state.get("selected_formula", "unknown")
    formula_confidence = state.get("formula_confidence", 0.0)

    decision = DecisionRecord(
        decision_id=decision_id,
        timestamp=datetime.now(UTC),
        user_id=effective_user_id,
        asset=asset,
        asset_type="stock",
        signal_score=formula_score,
        strategy_id=strategy_dict.get("id", "unknown"),
        strategy_name=strategy_dict.get("name", "unknown"),
        action=action,
        threshold_crossed=threshold_crossed,
        reasoning=reasoning,
        components=signal_dict.get("components", {}),
        correlation_id=correlation_id,
        reference_price=signal_dict.get("reference_price"),
    )

    # Record decision count
    stock_agent_decisions_total.labels(asset=asset, action=action).inc()

    # Store in memory-service
    try:
        memory_record = decision.to_memory_record()
        httpx.post(
            f"{MEMORY_SERVICE_BASE_URL}/memory/records",
            json=memory_record.model_dump(mode="json"),
            headers={"X-User-ID": effective_user_id},
            timeout=5.0,
        )
    except Exception as exc:
        errors.append(f"record_persist: {exc}")
        logger.warning("graph_record_persist_failed", extra={"error": str(exc)[:200]})

    duration = round((time.monotonic() - t0) * 1000, 2)
    timings = dict(state.get("phase_timings") or {})
    timings["record"] = duration

    logger.info("graph_record", extra={
        "decision_id": decision_id, "asset": asset, "action": action,
    })
    stock_agent_phase_total.labels(phase="record", status="ok").inc()

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

    workflow.add_conditional_edges(
        "gather",
        lambda s: "abort" if s.get("abort") else "detect",
        {"abort": END, "detect": "detect"},
    )
    workflow.add_edge("detect", "recall")
    workflow.add_edge("recall", "select")
    workflow.add_edge("select", "score")
    workflow.add_conditional_edges(
        "score",
        lambda s: "skip" if s.get("action") == "HOLD" and not s.get("threshold_crossed") else "check",
        {"skip": "record", "check": "check"},
    )
    workflow.add_edge("check", "execute")
    workflow.add_edge("execute", "record")
    workflow.add_edge("record", END)

    return workflow.compile()


agent_graph = build_agent_graph()
