import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    feature_store_base_url: str = os.getenv("FEATURE_STORE_BASE_URL", "http://localhost:8002")
    signal_threshold: float = float(os.getenv("SIGNAL_THRESHOLD", "0.6"))
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"


settings = Settings()
