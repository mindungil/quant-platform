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


VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"ACTIVE", "ARCHIVED"},
    "ACTIVE": {"PAUSED", "ARCHIVED"},
    "PAUSED": {"ACTIVE", "ARCHIVED"},
}


class Strategy(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_id: str = "anonymous"
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    version: str
    status: str = "DRAFT"
    backtest_results: dict[str, Any] = Field(default_factory=dict)
    shadow_metrics: dict[str, Any] = Field(default_factory=dict)


class StrategyStatusUpdate(BaseModel):
    status: str
