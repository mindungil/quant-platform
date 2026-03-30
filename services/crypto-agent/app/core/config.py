import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"
    jetstream_stream_name: str = os.getenv("SIGNAL_JETSTREAM_STREAM", "SIGNAL_DATA")
    signal_service_base_url: str = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    memory_service_base_url: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    llm_gateway_base_url: str = os.getenv("LLM_GATEWAY_BASE_URL", "http://localhost:8021")


settings = Settings()
