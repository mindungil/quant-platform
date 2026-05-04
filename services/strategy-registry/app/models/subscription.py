"""Models for user template subscriptions (Template lane)."""
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

UTC = timezone.utc

VALID_SUBSCRIPTION_STATUSES = {"enabled", "paused", "stopped"}


class TemplateSubscription(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    template_id: str
    asset_type: str = "crypto"
    status: str = "enabled"
    weight: float = 1.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TemplateSubscriptionCreate(BaseModel):
    template_id: str
    asset_type: str = "crypto"
    weight: float = 1.0

    @field_validator("weight")
    @classmethod
    def _w(cls, v: float) -> float:
        if v <= 0 or v > 10:
            raise ValueError("weight must be in (0, 10]")
        return v


class TemplateSubscriptionUpdate(BaseModel):
    status: str | None = None
    weight: float | None = None

    @field_validator("status")
    @classmethod
    def _s(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_SUBSCRIPTION_STATUSES:
            raise ValueError(f"status must be one of {VALID_SUBSCRIPTION_STATUSES}")
        return v

    @field_validator("weight")
    @classmethod
    def _w(cls, v: float | None) -> float | None:
        if v is not None and (v <= 0 or v > 10):
            raise ValueError("weight must be in (0, 10]")
        return v


class LaneAllocation(BaseModel):
    user_id: str
    asset_type: str = "crypto"
    agent_pct: float = 0.70
    template_pct: float = 0.30
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LaneAllocationUpdate(BaseModel):
    asset_type: str = "crypto"
    agent_pct: float
    template_pct: float

    @field_validator("agent_pct", "template_pct")
    @classmethod
    def _in_range(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("pct must be in [0, 1]")
        return v
