from datetime import UTC, datetime

from pydantic import BaseModel, Field


class PositionUpdate(BaseModel):
    user_id: str
    asset: str
    side: str
    quantity: float
    price: float = 0.0
    notional: float = 0.0
    order_id: str | None = None


class PortfolioSnapshot(BaseModel):
    user_id: str
    positions: dict[str, float] = Field(default_factory=dict)
    average_entry_prices: dict[str, float] = Field(default_factory=dict)
    recent_fills: list[PositionUpdate] = Field(default_factory=list)
    total_exposure: float = 0.0
    rebalance_needed: bool = False
    updated_at: datetime | None = Field(default_factory=lambda: datetime.now(UTC))
