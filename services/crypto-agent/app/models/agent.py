from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class SignalSnapshot(BaseModel):
    asset: str
    signal_score: float
    threshold: float
    threshold_crossed: bool
    direction: str
    components: dict[str, float]
    feature_timestamp: datetime


class StrategySnapshot(BaseModel):
    id: str
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
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
    id: str | None = None
    timestamp: datetime | None = None
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


class DecisionRecord(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    asset: str
    asset_type: str
    signal_score: float
    strategy_id: str
    strategy_name: str
    action: str
    threshold_crossed: bool
    reasoning: str
    memory_refs: list[str]
    components: dict[str, float]

    def to_memory_record(self) -> MemoryRecord:
        return MemoryRecord(
            asset=self.asset,
            asset_type=self.asset_type,
            signal_score=self.signal_score,
            action=self.action,
            strategy_id=self.strategy_id,
            reasoning=self.reasoning,
            metadata={
                "strategy_name": self.strategy_name,
                "memory_refs": self.memory_refs,
                "components": self.components,
            },
        )
