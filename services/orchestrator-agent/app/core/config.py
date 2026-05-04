import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    statistics_service_base_url: str = os.getenv("STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    crypto_agent_base_url: str = os.getenv("CRYPTO_AGENT_BASE_URL", "http://localhost:8006")
    etf_agent_base_url: str = os.getenv("ETF_AGENT_BASE_URL", "http://localhost:8015")
    stock_agent_base_url: str = os.getenv("STOCK_AGENT_BASE_URL", "http://localhost:8016")
    signal_service_base_url: str = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    memory_service_base_url: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
    market_data_base_url: str = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")
    feature_store_base_url: str = os.getenv("FEATURE_STORE_BASE_URL", "http://localhost:8002")
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")


settings = Settings()
