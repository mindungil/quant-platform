from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "anonymous"
    asset: str
    asset_type: str
    signal_score: float
    action: str
    strategy_id: str | None = None
    reasoning: str
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class MemorySearchResponse(BaseModel):
    query: MemorySearchRequest
    items: list[MemorySearchResult]
