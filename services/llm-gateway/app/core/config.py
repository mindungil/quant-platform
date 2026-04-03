import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider_name: str = os.getenv("LLM_PROVIDER_NAME", "deterministic-bootstrap")
    # LLM API keys — LiteLLM reads these from env automatically
    # Supported: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY
    openai_api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("GRAPHRAG_API_KEY", ""))
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    # Default model: litellm format — provider/model
    default_model: str = os.getenv("LLM_DEFAULT_MODEL", "gpt-4o-mini")
    # Fallback to template if LLM fails
    enable_llm: bool = os.getenv("ENABLE_LLM", "true").lower() == "true"
    # Max tokens for reasoning
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "500"))


settings = Settings()
