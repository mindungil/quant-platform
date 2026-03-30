import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"


settings = Settings()
