from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CandleCollectorStatus(BaseModel):
    provider: str
    asset: str
    enabled: bool
    mode: str


class CandlePayload(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float

    @field_validator("high")
    @classmethod
    def high_must_cover_open(cls, value: float, info) -> float:
        open_value = info.data.get("open")
        if open_value is not None and value < open_value:
            raise ValueError("high must be >= open")
        return value

    @field_validator("low")
    @classmethod
    def low_must_not_exceed_open(cls, value: float, info) -> float:
        open_value = info.data.get("open")
        if open_value is not None and value > open_value:
            raise ValueError("low must be <= open")
        return value


class ValidationResult(BaseModel):
    accepted: bool
    anomaly_detected: bool
    reason: str


class CandleIngestResponse(BaseModel):
    asset: str
    accepted: bool
    anomaly_detected: bool
    event_subject: str


class CandleUpdatedEvent(BaseModel):
    asset: str
    subject: str
    anomaly_detected: bool
    candle: CandlePayload
