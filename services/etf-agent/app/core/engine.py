"""ETF-agent decision engine — delegates to LangGraph StateGraph."""
import logging
from datetime import datetime, timezone

UTC = timezone.utc

from app.core.market_hours import is_korean_market_open
from app.core.graph import agent_graph
from app.models.agent import DecisionRecord

logger = logging.getLogger("etf-agent")


def run_decision_loop(
    asset: str,
    *,
    user_id: str | None = None,
    correlation_id: str | None = None,
) -> DecisionRecord:
    """Execute a single decision cycle for the given ETF asset.

    If the Korean market is closed the agent immediately returns a HOLD decision.
    Otherwise delegates to the full 8-phase LangGraph decision loop.
    """
    now = datetime.now()
    market_open = is_korean_market_open(now)

    if not market_open:
        return DecisionRecord(
            timestamp=datetime.now(UTC),
            asset=asset,
            asset_type="etf",
            action="HOLD",
            signal_score=0.0,
            reasoning="market_closed",
            market_open=False,
            correlation_id=correlation_id,
        )

    # Invoke the LangGraph StateGraph
    initial_state = {
        "asset": asset,
        "user_id": user_id,
        "correlation_id": correlation_id,
        "signal": None,
        "signal_age_seconds": None,
        "regime": None,
        "suggested_formula_type": None,
        "features": None,
        "formula_scores": None,
        "strategy": None,
        "effective_user_id": user_id or "bootstrap",
        "selected_formula": None,
        "formula_score": None,
        "formula_confidence": None,
        "risk_issues": [],
        "action": "HOLD",
        "threshold_crossed": False,
        "decision_id": None,
        "order_request": None,
        "order_submitted": False,
        "reasoning": None,
        "recorded": False,
        "errors": [],
        "phase_timings": {},
        "abort": False,
    }

    try:
        result = agent_graph.invoke(initial_state)
    except Exception as exc:
        logger.error("graph_invoke_failed", extra={"asset": asset, "error": str(exc)[:300]})
        return DecisionRecord(
            timestamp=datetime.now(UTC),
            asset=asset,
            asset_type="etf",
            action="HOLD",
            signal_score=0.0,
            reasoning=f"graph_error: {exc}",
            market_open=True,
            correlation_id=correlation_id,
        )

    # Build DecisionRecord from graph result
    signal_dict = result.get("signal") or {}
    strategy_dict = result.get("strategy") or {}

    decision = DecisionRecord(
        decision_id=result.get("decision_id") or None,
        timestamp=datetime.now(UTC),
        user_id=result.get("effective_user_id", "bootstrap"),
        asset=asset,
        asset_type="etf",
        action=result.get("action", "HOLD"),
        signal_score=result.get("formula_score") or 0.0,
        strategy_id=strategy_dict.get("id", "unknown"),
        strategy_name=strategy_dict.get("name", "unknown"),
        threshold_crossed=result.get("threshold_crossed", False),
        reasoning=result.get("reasoning") or "",
        components=signal_dict.get("components", {}),
        correlation_id=correlation_id,
        reference_price=signal_dict.get("reference_price"),
        market_open=True,
    )

    if result.get("errors"):
        logger.warning("graph_completed_with_errors", extra={
            "asset": asset, "errors": result["errors"],
        })

    logger.info("decision_complete", extra={
        "asset": asset,
        "action": decision.action,
        "score": decision.signal_score,
        "phase_timings": result.get("phase_timings", {}),
    })

    return decision
