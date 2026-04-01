import os
from dataclasses import dataclass

from shared.runtime import env_bool


@dataclass(frozen=True)
class Settings:
    auth_service_base_url: str = os.getenv("AUTH_SERVICE_BASE_URL", "http://localhost:8019")
    market_data_base_url: str = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")
    feature_store_base_url: str = os.getenv("FEATURE_STORE_BASE_URL", "http://localhost:8002")
    signal_service_base_url: str = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    memory_service_base_url: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    crypto_agent_base_url: str = os.getenv("CRYPTO_AGENT_BASE_URL", "http://localhost:8006")
    backtest_service_base_url: str = os.getenv("BACKTEST_SERVICE_BASE_URL", "http://localhost:8007")
    order_service_base_url: str = os.getenv("ORDER_SERVICE_BASE_URL", "http://localhost:8011")
    credential_store_base_url: str = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://localhost:8010")
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    statistics_service_base_url: str = os.getenv("STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")
    external_data_service_base_url: str = os.getenv("EXTERNAL_DATA_SERVICE_BASE_URL", "http://localhost:8020")
    llm_gateway_base_url: str = os.getenv("LLM_GATEWAY_BASE_URL", "http://localhost:8021")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_issuer: str = os.getenv("JWT_ISSUER", "quant-auth-service")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    realtime_replay_limit: int = int(os.getenv("REALTIME_REPLAY_LIMIT", "200"))
    internal_admin_secret: str = os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret")
    strict_runtime: bool = env_bool("STRICT_RUNTIME", False)
    live_trading_enabled: bool = env_bool("LIVE_TRADING_ENABLED", False)
    default_shadow_mode: bool = env_bool("DEFAULT_SHADOW_MODE", True)
    allowed_live_exchanges: tuple[str, ...] = tuple(
        item.strip().lower() for item in os.getenv("ALLOWED_LIVE_EXCHANGES", "binance").split(",") if item.strip()
    )


settings = Settings()
