import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"
    execution_jetstream_stream: str = os.getenv("EXECUTION_JETSTREAM_STREAM", "EXECUTION_DATA")
    internal_admin_secret: str = os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret")
    admin_header_ttl_seconds: int = int(os.getenv("INTERNAL_ADMIN_HEADER_TTL_SECONDS", "300"))
    realtime_replay_limit: int = int(os.getenv("REALTIME_REPLAY_LIMIT", "200"))


settings = Settings()
