from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SignalSnapshot(BaseModel):
    asset: str
    asset_type: str = "crypto"
    strategy_id: str | None = None
    strategy_user_id: str | None = None
    signal_score: float
    threshold: float
    threshold_crossed: bool
    direction: str
    components: dict[str, float]
    feature_timestamp: datetime
    reference_price: float | None = None
    # Raw indicator pass-through (signal-service already produces these in its
    # FeatureSnapshot model; this layer needs them so formula.compute() in
    # graph.detect_node can call features.get('ema_9') and friends — without
    # these, every formula returns confidence=0 → every decision becomes HOLD →
    # no fills → MAB starves → V3 learning loop processes 0 alphas.
    close: float | None = None
    volume: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    ema_9: float | None = None
    ema_21: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    stochastic_k: float | None = None
    stochastic_d: float | None = None
    vwap: float | None = None
    atr_14: float | None = None
    adx_14: float | None = None
    obv: float | None = None


class StrategySnapshot(BaseModel):
    id: str
    user_id: str = "anonymous"
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    version: str
    status: str


class MemorySearchRequest(BaseModel):
    user_id: str = "anonymous"
    asset: str
    asset_type: str = "crypto"
    signal_score: float
    action: str | None = None
    strategy_id: str | None = None
    top_k: int = 5


class MemoryRecord(BaseModel):
    id: str | None = None
    timestamp: datetime | None = None
    user_id: str = "anonymous"
    asset: str
    asset_type: str
    signal_score: float
    action: str
    strategy_id: str | None = None
    reasoning: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchResult(BaseModel):
    score: float
    record: MemoryRecord


class MemorySearchResponse(BaseModel):
    query: MemorySearchRequest
    items: list[MemorySearchResult]


class PhaseResult(BaseModel):
    name: str
    status: str = "pending"  # pending | completed | skipped | failed
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    detail: str | None = None


class DecisionRecord(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "anonymous"
    asset: str
    asset_type: str
    signal_score: float
    strategy_id: str
    strategy_name: str
    action: str
    threshold_crossed: bool
    reasoning: str
    memory_refs: list[str]
    # Accepts numeric factor scores plus optional string labels (regime,
    # style_formula) that graph.py attaches for hindsight/MAB attribution.
    components: dict[str, Any]
    correlation_id: str | None = None
    reference_price: float | None = None
    decision_phases: list[PhaseResult] = Field(default_factory=list)

    def to_memory_record(self) -> MemoryRecord:
        return MemoryRecord(
            user_id=self.user_id,
            asset=self.asset,
            asset_type=self.asset_type,
            signal_score=self.signal_score,
            action=self.action,
            strategy_id=self.strategy_id,
            reasoning=self.reasoning,
            memory_type="episode",
            metadata={
                "decision_id": self.decision_id,
                "strategy_name": self.strategy_name,
                "memory_refs": self.memory_refs,
                "components": self.components,
                "correlation_id": self.correlation_id,
                "reference_price": self.reference_price,
            },
        )
