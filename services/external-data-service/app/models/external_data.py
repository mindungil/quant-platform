from datetime import datetime

from pydantic import BaseModel, Field


class ExternalContextSnapshot(BaseModel):
    asset: str
    timestamp: datetime
    source_timestamp: datetime | None = None
    news_sentiment: float | None = None
    onchain_score: float | None = None
    macro_risk_score: float | None = None
    fear_greed_index: int | None = None
    btc_dominance: float | None = None
    market_cap_change_24h: float | None = None
    volume_score: float | None = None
    price_change_24h: float | None = None
    altcoin_season: bool | None = None
    funding_rate: float | None = None
    funding_rate_score: float | None = None
    open_interest_score: float | None = None
    long_short_ratio: float | None = None
    long_short_score: float | None = None
    taker_buy_sell_ratio: float | None = None
    taker_buy_sell_score: float | None = None
    derivatives_sentiment: float | None = None
    components: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    degraded_mode: bool = False
    stale: bool = False
    source: str = "live"
