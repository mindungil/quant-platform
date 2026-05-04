from datetime import datetime

from pydantic import BaseModel, Field


class FeatureSnapshot(BaseModel):
    asset: str
    timestamp: datetime
    close: float | None = None
    volume: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    ema_9: float | None = None
    ema_21: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    stochastic_k: float | None = None
    stochastic_d: float | None = None
    vwap: float | None = None
    atr_14: float | None = None
    adx_14: float | None = None
    obv: float | None = None


class ExternalContextSnapshot(BaseModel):
    asset: str
    timestamp: datetime
    source_timestamp: datetime | None = None
    news_sentiment: float | None = None
    onchain_score: float | None = None
    macro_risk_score: float | None = None
    fear_greed_index: int | None = None
    components: dict[str, float] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    degraded_mode: bool = False
    stale: bool = False
    source: str = "live"


class SignalEvaluationResponse(BaseModel):
    asset: str
    asset_type: str = "crypto"
    strategy_id: str | None = None
    strategy_user_id: str | None = None
    signal_score: float
    threshold: float
    threshold_crossed: bool
    direction: str
    components: dict[str, float]
    feature_timestamp: datetime
    external_timestamp: datetime | None = None
    reference_price: float | None = None


class SignalThresholdEvent(BaseModel):
    asset: str
    asset_type: str
    subject: str
    evaluation: SignalEvaluationResponse
