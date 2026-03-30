from datetime import datetime

from pydantic import BaseModel


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


class SignalEvaluationResponse(BaseModel):
    asset: str
    signal_score: float
    threshold: float
    threshold_crossed: bool
    direction: str
    components: dict[str, float]
    feature_timestamp: datetime


class SignalThresholdEvent(BaseModel):
    asset: str
    asset_type: str
    subject: str
    evaluation: SignalEvaluationResponse
