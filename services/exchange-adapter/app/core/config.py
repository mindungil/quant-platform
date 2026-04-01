import os
from dataclasses import dataclass

from shared.runtime import env_bool


@dataclass(frozen=True)
class Settings:
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    binance_api_base_url: str = os.getenv("BINANCE_API_BASE_URL", "https://api.binance.com")
    upbit_api_base_url: str = os.getenv("UPBIT_API_BASE_URL", "https://api.upbit.com")
    alpaca_api_base_url: str = os.getenv("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets")
    strict_runtime: bool = env_bool("STRICT_RUNTIME", False)


settings = Settings()
