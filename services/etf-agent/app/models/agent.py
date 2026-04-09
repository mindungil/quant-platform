from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SignalSnapshot(BaseModel):
    asset: str
    asset_type: str = "etf"
    strategy_id: str | None = None
    strategy_user_id: str | None = None
    signal_score: float
    threshold: float = 0.4
    threshold_crossed: bool = False
    direction: str = "HOLD"
    components: dict[str, float] = Field(default_factory=dict)
    feature_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reference_price: float | None = None


class StrategySnapshot(BaseModel):
    id: str
    user_id: str = "anonymous"
    name: str
    asset_type: str = "etf"
    indicators: list[str] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    thresholds: dict[str, float] = Field(default_factory=dict)
    version: str = "1"
    status: str = "ACTIVE"


class MemoryRecord(BaseModel):
    id: str | None = None
    timestamp: datetime | None = None
    user_id: str = "anonymous"
    asset: str
    asset_type: str = "etf"
    signal_score: float
    action: str
    strategy_id: str | None = None
    reasoning: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionRecord(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "anonymous"
    asset: str
    asset_type: str = "etf"
    action: str = "HOLD"
    signal_score: float = 0.0
    strategy_id: str = "unknown"
    strategy_name: str = "unknown"
    threshold_crossed: bool = False
    reasoning: str = ""
    memory_refs: list[str] = Field(default_factory=list)
    components: dict[str, float] = Field(default_factory=dict)
    correlation_id: str | None = None
    reference_price: float | None = None
    market_open: bool = True

    def to_memory_record(self) -> MemoryRecord:
        return MemoryRecord(
            user_id=self.user_id,
            asset=self.asset,
            asset_type=self.asset_type,
            signal_score=self.signal_score,
            action=self.action,
            strategy_id=self.strategy_id,
            reasoning=self.reasoning,
            metadata={
                "decision_id": self.decision_id,
                "strategy_name": self.strategy_name,
                "memory_refs": self.memory_refs,
                "components": self.components,
                "correlation_id": self.correlation_id,
                "reference_price": self.reference_price,
            },
        )
