from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class StrategyCreate(BaseModel):
    user_id: str = "anonymous"
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    version: str = "v1"


class Strategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "anonymous"
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    version: str
    status: str = "PENDING"
    backtest_results: dict[str, Any] = Field(default_factory=dict)
    shadow_metrics: dict[str, Any] = Field(default_factory=dict)


class StrategyStatusUpdate(BaseModel):
    status: str
