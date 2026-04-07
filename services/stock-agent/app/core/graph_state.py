"""AgentState TypedDict for Stock-agent LangGraph StateGraph decision loop."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict):
    # Input
    asset: str
    user_id: Optional[str]
    correlation_id: Optional[str]

    # Phase 1: Gather
    signal: Optional[Dict]
    signal_age_seconds: Optional[float]

    # Phase 2: Detect
    regime: Optional[str]
    suggested_formula_type: Optional[str]
    features: Optional[Dict]

    # Phase 3: Recall (memory)
    formula_scores: Optional[Dict]

    # Phase 4: Select strategy
    strategy: Optional[Dict]
    effective_user_id: str

    # Phase 5: Score (run formula)
    selected_formula: Optional[str]
    formula_score: Optional[float]
    formula_confidence: Optional[float]

    # Phase 6: Check (risk pre-check)
    risk_issues: List[str]
    action: str  # BUY / SELL / HOLD
    threshold_crossed: bool

    # Phase 7: Execute
    decision_id: Optional[str]
    order_request: Optional[Dict]
    order_submitted: bool
    reasoning: Optional[str]

    # Phase 8: Record
    recorded: bool

    # Error tracking
    errors: List[str]
    phase_timings: Dict[str, float]
    abort: bool
