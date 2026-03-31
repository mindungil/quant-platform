import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    realtime_replay_limit: int = int(os.getenv("REALTIME_REPLAY_LIMIT", "200"))


settings = Settings()
