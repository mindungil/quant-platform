from datetime import datetime, timezone

UTC = timezone.utc
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# Shadow promotion criteria — hardened to reduce false promotions.
# Rationale: 10 trades over 14 days is statistically meaningless for Sharpe estimation.
# Minimum 30 trades gives ~80% confidence interval within ±0.5 Sharpe units.
# 28 days ensures at least one full regime cycle in crypto markets.
SHADOW_DURATION_DAYS = 28
SHADOW_MIN_TRADES = 30
SHADOW_MIN_SHARPE = 0.8


class StrategyCreate(BaseModel):
    user_id: str = "anonymous"
    name: str
    asset_type: str
    indicators: list[str]
    weights: dict[str, float]
    thresholds: dict[str, float]
    version: str = "v1"


VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"TESTED", "ACTIVE", "ARCHIVED"},
    "TESTED": {"SHADOW", "ACTIVE", "ARCHIVED"},
    "SHADOW": {"ACTIVE", "DEPRECATED", "ARCHIVED"},
    "ACTIVE": {"PAUSED", "DEPRECATED", "ARCHIVED"},
    "PAUSED": {"ACTIVE", "ARCHIVED"},
    "DEPRECATED": {"ARCHIVED"},
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
    shadow_start_at: datetime | None = None


class StrategyStatusUpdate(BaseModel):
    status: str


class ShadowMetricsUpdate(BaseModel):
    """Payload for updating shadow metrics after a shadow trade."""
    pnl: float = 0.0
    trade_count: int = 0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
