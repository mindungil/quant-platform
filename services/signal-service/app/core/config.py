import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    feature_store_base_url: str = os.getenv("FEATURE_STORE_BASE_URL", "http://localhost:8002")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    external_data_service_base_url: str = os.getenv("EXTERNAL_DATA_SERVICE_BASE_URL", "http://localhost:8020")
    signal_threshold: float = float(os.getenv("SIGNAL_THRESHOLD", "0.6"))
    external_signal_weight: float = float(os.getenv("EXTERNAL_SIGNAL_WEIGHT", "0.35"))
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    timescale_url: str = os.getenv("TIMESCALE_URL", "postgresql+psycopg://postgres:postgres@localhost:5433/market")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"
    jetstream_stream_name: str = os.getenv("SIGNAL_JETSTREAM_STREAM", "SIGNAL_DATA")


settings = Settings()
