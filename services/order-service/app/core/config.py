import os
from dataclasses import dataclass

from shared.runtime import env_bool


@dataclass(frozen=True)
class Settings:
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    exchange_adapter_base_url: str = os.getenv("EXCHANGE_ADAPTER_BASE_URL", "http://localhost:8008")
    credential_store_base_url: str = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://localhost:8010")
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    statistics_service_base_url: str = os.getenv("STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    enable_nats: bool = env_bool("ENABLE_NATS", True)
    execution_jetstream_stream: str = os.getenv("EXECUTION_JETSTREAM_STREAM", "EXECUTION_DATA")
    realtime_replay_limit: int = int(os.getenv("REALTIME_REPLAY_LIMIT", "200"))
    strict_runtime: bool = env_bool("STRICT_RUNTIME", False)
    live_trading_enabled: bool = env_bool("LIVE_TRADING_ENABLED", False)
    allowed_live_exchanges: tuple[str, ...] = tuple(
        item.strip().lower() for item in os.getenv("ALLOWED_LIVE_EXCHANGES", "binance,upbit").split(",") if item.strip()
    )
    default_shadow_mode: bool = env_bool("DEFAULT_SHADOW_MODE", True)
    internal_admin_secret: str = os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret")
    admin_header_ttl_seconds: int = int(os.getenv("INTERNAL_ADMIN_HEADER_TTL_SECONDS", "300"))


settings = Settings()
