import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    exchange_adapter_base_url: str = os.getenv("EXCHANGE_ADAPTER_BASE_URL", "http://localhost:8008")
    credential_store_base_url: str = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://localhost:8010")
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    statistics_service_base_url: str = os.getenv("STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    realtime_replay_limit: int = int(os.getenv("REALTIME_REPLAY_LIMIT", "200"))


settings = Settings()
