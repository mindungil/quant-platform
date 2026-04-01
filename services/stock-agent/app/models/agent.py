from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class DecisionRecord(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    asset: str
    asset_type: str = "stock"
    action: str  # BUY | SELL | HOLD
    signal_score: float = 0.0
    threshold_crossed: bool = False
    reasoning: str = ""
    components: dict[str, float] = Field(default_factory=dict)
    correlation_id: str | None = None
    reference_price: float | None = None
    market_open: bool = True
