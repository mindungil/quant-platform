from datetime import datetime

from pydantic import BaseModel, Field


class CandlePayload(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float


class FeatureResponse(BaseModel):
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


class FeatureUpdatedEvent(BaseModel):
    asset: str
    subject: str
    feature: FeatureResponse
