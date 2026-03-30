import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    auth_service_base_url: str = os.getenv("AUTH_SERVICE_BASE_URL", "http://localhost:8019")
    signal_service_base_url: str = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    memory_service_base_url: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    order_service_base_url: str = os.getenv("ORDER_SERVICE_BASE_URL", "http://localhost:8011")
    credential_store_base_url: str = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://localhost:8010")
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    statistics_service_base_url: str = os.getenv("STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_issuer: str = os.getenv("JWT_ISSUER", "quant-auth-service")


settings = Settings()
