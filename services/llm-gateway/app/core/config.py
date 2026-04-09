import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Server public endpoint (for OAuth redirect_uri)
    public_host: str = os.getenv("PUBLIC_HOST", "quent.kro.kr")
    public_scheme: str = os.getenv("PUBLIC_SCHEME", "https")
    llm_gateway_port: int = int(os.getenv("LLM_GATEWAY_PORT", "8021"))
    # LLM은 OAuth 기반 — API 키 불필요 (유저가 Claude/Codex 구독으로 인증)
    enable_llm: bool = os.getenv("ENABLE_LLM", "true").lower() == "true"
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "500"))
    agent_max_tokens: int = int(os.getenv("AGENT_MAX_TOKENS", "2000"))
    agent_max_loops: int = int(os.getenv("AGENT_MAX_LOOPS", "10"))
    # Internal service URLs
    market_data_base_url: str = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")
    feature_store_base_url: str = os.getenv("FEATURE_STORE_BASE_URL", "http://localhost:8002")
    signal_service_base_url: str = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    memory_service_base_url: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    backtest_service_base_url: str = os.getenv("BACKTEST_SERVICE_BASE_URL", "http://localhost:8007")
    order_service_base_url: str = os.getenv("ORDER_SERVICE_BASE_URL", "http://localhost:8011")
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    risk_service_base_url: str = os.getenv("RISK_SERVICE_BASE_URL", "http://localhost:8009")
    # Security
    internal_admin_secret: str = os.getenv("INTERNAL_ADMIN_SECRET", "dev-internal-admin-secret")
    admin_header_ttl_seconds: int = int(os.getenv("INTERNAL_ADMIN_HEADER_TTL_SECONDS", "300"))
    # Database
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@db:5432/platform")


settings = Settings()
