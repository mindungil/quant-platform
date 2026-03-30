import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    timescale_url: str = os.getenv("TIMESCALE_URL", "postgresql+psycopg://postgres:postgres@localhost:5433/market")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"
    jetstream_stream_name: str = os.getenv("FEATURE_JETSTREAM_STREAM", "FEATURE_DATA")


settings = Settings()
