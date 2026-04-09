"""Hindsight Analysis — 에이전트 결정의 사후 검증.

매 결정 후 24시간 가격 변화를 추적하여 적중률을 계산.
자본 없이도 에이전트의 판단 품질을 검증할 수 있다.
"""
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("statistics-service")


def analyze_decision_hindsight(
    decision: dict,
    current_price: float,
    decision_price: float,
) -> dict:
    """Analyze a single decision against actual price movement.

    Returns:
        {
            "decision_id": str,
            "asset": str,
            "action": str,  # BUY/SELL/HOLD
            "decision_price": float,
            "current_price": float,
            "price_change_pct": float,  # actual price change
            "correct": bool,  # was the decision right?
            "score": float,  # -1 to 1 (how right/wrong)
        }
    """
    action = decision.get("action", "HOLD")
    price_change_pct = ((current_price - decision_price) / decision_price) * 100 if decision_price > 0 else 0

    # Determine correctness
    # Asymmetric scoring: BUY/SELL judged on direction with magnitude reward,
    # HOLD judged on whether the market actually stayed quiet. The previous
    # version penalized HOLDs for any large move (even though HOLD avoids loss
    # in a downturn), unfairly tanking the accuracy metric.
    HOLD_QUIET_THRESHOLD = 1.5  # ±1.5% defines a "quiet" market
    if action == "BUY":
        correct = price_change_pct > 0
        score = max(-1.0, min(1.0, price_change_pct / 5.0))  # signed reward, ±5% saturates
    elif action == "SELL":
        correct = price_change_pct < 0
        score = max(-1.0, min(1.0, -price_change_pct / 5.0))
    else:  # HOLD
        if abs(price_change_pct) < HOLD_QUIET_THRESHOLD:
            # Quiet market: HOLD was the right call
            correct = True
            score = 1.0 - abs(price_change_pct) / HOLD_QUIET_THRESHOLD
        else:
            # Market moved meaningfully: HOLD missed an opportunity, but is
            # NOT "wrong" the way a wrong-direction trade is wrong. Treat it
            # as neutral (score 0, not counted as correct).
            correct = False
            score = 0.0

    return {
        "decision_id": decision.get("decision_id"),
        "asset": decision.get("asset"),
        "action": action,
        "decision_price": decision_price,
        "current_price": current_price,
        "price_change_pct": round(price_change_pct, 2),
        "correct": correct,
        "score": round(score, 4),
        "hours_elapsed": decision.get("hours_elapsed", 0),
    }


def compute_accuracy_report(analyses: list[dict]) -> dict:
    """Compute accuracy metrics from a list of hindsight analyses.

    Returns weekly/monthly accuracy report.
    """
    if not analyses:
        return {"total": 0, "accuracy": 0, "by_action": {}}

    total = len(analyses)
    correct = sum(1 for a in analyses if a["correct"])
    accuracy = correct / total if total > 0 else 0

    # By action type
    by_action = {}
    for action in ["BUY", "SELL", "HOLD"]:
        action_items = [a for a in analyses if a["action"] == action]
        if action_items:
            action_correct = sum(1 for a in action_items if a["correct"])
            by_action[action] = {
                "count": len(action_items),
                "correct": action_correct,
                "accuracy": round(action_correct / len(action_items), 4),
                "avg_score": round(sum(a["score"] for a in action_items) / len(action_items), 4),
            }

    # Average score
    avg_score = sum(a["score"] for a in analyses) / total

    return {
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "avg_score": round(avg_score, 4),
        "by_action": by_action,
        "period_start": min(a.get("decision_id", "") for a in analyses),
        "period_end": max(a.get("decision_id", "") for a in analyses),
    }
