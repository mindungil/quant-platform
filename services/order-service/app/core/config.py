import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    exchange_adapter_base_url: str = os.getenv("EXCHANGE_ADAPTER_BASE_URL", "http://localhost:8008")
    credential_store_base_url: str = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://localhost:8010")


settings = Settings()
