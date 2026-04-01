import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider_name: str = os.getenv("LLM_PROVIDER_NAME", "deterministic-bootstrap")


settings = Settings()
