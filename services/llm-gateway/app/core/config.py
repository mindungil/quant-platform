import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider_name: str = os.getenv("LLM_PROVIDER_NAME", "litellm")
    # API Keys — 하나만 설정하면 됨
    openai_api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("GRAPHRAG_API_KEY", ""))
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    # 모델 선택 (LiteLLM 형식)
    # OpenAI: gpt-4o, gpt-4o-mini
    # Anthropic: claude-3-5-sonnet-20241022
    default_model: str = os.getenv("LLM_DEFAULT_MODEL", "gpt-4o-mini")
    enable_llm: bool = os.getenv("ENABLE_LLM", "true").lower() == "true"
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "500"))


settings = Settings()
