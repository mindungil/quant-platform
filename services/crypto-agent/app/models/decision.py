from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    SKIP = "SKIP"
    ERROR = "ERROR"


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    SKIP = "SKIP"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Data transfer models
# ---------------------------------------------------------------------------

class SignalData(BaseModel):
    asset: str
    signal_score: float
    threshold: float
    threshold_crossed: bool
    direction: str
    components: dict[str, float]
    feature_timestamp: str | None = None


class FeatureData(BaseModel):
    features: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime | None = None


class StrategyData(BaseModel):
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict
    thresholds: dict
    version: str
    status: str


class MemorySearchRequest(BaseModel):
    asset: str
    asset_type: str = "crypto"
    signal_score: float
    action: str | None = None
    strategy_id: str | None = None
    top_k: int = 5


class MemoryRecord(BaseModel):
    asset: str
    asset_type: str
    signal_score: float
    action: str | None = None
    strategy_id: str | None = None
    reasoning: str | None = None
    metadata: dict = Field(default_factory=dict)


class MemorySearchResult(BaseModel):
    score: float
    record: MemoryRecord


class MemorySearchResponse(BaseModel):
    query: MemorySearchRequest
    items: list[MemorySearchResult]


class RiskApproval(BaseModel):
    approved: bool
    reason: str
    level: str | None = None


class OrderRequest(BaseModel):
    user_id: str
    exchange: str
    asset: str
    side: str
    quantity: float
    requested_notional: float
    max_notional: float
    current_drawdown: float
    shadow_mode: bool = False


class OrderResult(BaseModel):
    approved: bool
    reason: str
    risk_reason: str | None = None
    order_id: str | None = None
    shadow_mode: bool = False


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------

class AgentState(BaseModel):
    asset: str
    signal: Optional[SignalData] = None
    features: Optional[dict] = None
    memories: list = Field(default_factory=list)
    strategy: Optional[StrategyData] = None
    risk_approval: Optional[RiskApproval] = None
    order_result: Optional[OrderResult] = None
    decision: Optional[DecisionRecord] = None
    action: str | None = None
    error: str | None = None
    init: bool = True
    step: str | None = None
    adjusted_score: Optional[float] = None
    strategy_weighted_score: float | None = None
    memory_refs: list = Field(default_factory=list)
    memory_insight: str | None = None


class DecisionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    asset: str
    agent_type: str = "crypto"
    user_id: str = "system"
    signal_score: float | None = None
    direction: str | None = None
    action: str | None = None
    strategy: str | None = None
    memory_refs: list = Field(default_factory=list)
    reasoning: str | None = None
    risk_approved: bool | None = None
    outcome: str | None = None
    order_id: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    shadow_mode: bool | None = None
    metadata: dict = Field(default_factory=dict)


# Update forward ref
AgentState.model_rebuild()


class AgentStatus(BaseModel):
    running: bool = True
    paused: bool = False
    error_count: int = 0
    total_decisions: int = 0
    last_decision_at: datetime | None = None
    last_asset: str | None = None
    last_action: str | None = None
    uptime_seconds: float | None = None
