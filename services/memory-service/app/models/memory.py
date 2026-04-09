from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "anonymous"
    memory_type: str = "episode"
    asset: str
    asset_type: str
    signal_score: float
    action: str
    strategy_id: str | None = None
    reasoning: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)
    formula_name: str | None = None
    regime_label: str | None = None
    trade_outcome: float | None = None     # realized PnL of the trade
    outcome_sharpe: float | None = None    # rolling Sharpe after this trade
    links: list[str] = Field(default_factory=list)
    link_weights: dict[str, float] = Field(default_factory=dict)
    last_reinforced_at: datetime | None = None


class MemorySearchRequest(BaseModel):
    user_id: str = "anonymous"
    asset: str
    asset_type: str = "crypto"
    signal_score: float
    action: str | None = None
    strategy_id: str | None = None
    top_k: int = 5


class MemorySearchResult(BaseModel):
    score: float
    record: MemoryRecord


class FormulaOutcomeSearchRequest(BaseModel):
    regime_label: str
    asset: str | None = None
    formula_name: str | None = None
    top_k: int = 10


class MemorySearchResponse(BaseModel):
    query: MemorySearchRequest | FormulaOutcomeSearchRequest
    items: list[MemorySearchResult]
