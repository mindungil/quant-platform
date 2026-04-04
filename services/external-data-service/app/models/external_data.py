from datetime import datetime

from pydantic import BaseModel, Field


class ExternalContextSnapshot(BaseModel):
    asset: str
    timestamp: datetime
    news_sentiment: float | None = None
    onchain_score: float | None = None
    macro_risk_score: float | None = None
    fear_greed_index: int | None = None
    btc_dominance: float | None = None
    market_cap_change_24h: float | None = None
    volume_score: float | None = None
    price_change_24h: float | None = None
    altcoin_season: bool | None = None
    components: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
