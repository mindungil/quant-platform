import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    nats_url: str = os.getenv("NATS_URL", "nats://localhost:4222")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    enable_nats: bool = os.getenv("ENABLE_NATS", "true").lower() == "true"
    jetstream_stream_name: str = os.getenv("SIGNAL_JETSTREAM_STREAM", "SIGNAL_DATA")
    execution_jetstream_stream: str = os.getenv("EXECUTION_JETSTREAM_STREAM", "EXECUTION_DATA")
    signal_service_base_url: str = os.getenv("SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    memory_service_base_url: str = os.getenv("MEMORY_SERVICE_BASE_URL", "http://localhost:8004")
    strategy_registry_base_url: str = os.getenv("STRATEGY_REGISTRY_BASE_URL", "http://localhost:8005")
    llm_gateway_base_url: str = os.getenv("LLM_GATEWAY_BASE_URL", "http://localhost:8021")
    postgres_url: str = os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    default_exchange: str = os.getenv("DEFAULT_AGENT_EXCHANGE", "binance")
    default_requested_notional: float = float(os.getenv("DEFAULT_AGENT_REQUESTED_NOTIONAL", "1000"))
    default_max_notional: float = float(os.getenv("DEFAULT_AGENT_MAX_NOTIONAL", "5000"))
    default_exposure_limit: float = float(os.getenv("DEFAULT_AGENT_EXPOSURE_LIMIT", "50000"))
    default_current_drawdown: float = float(os.getenv("DEFAULT_AGENT_CURRENT_DRAWDOWN", "0.01"))
    default_current_exposure: float = float(os.getenv("DEFAULT_AGENT_CURRENT_EXPOSURE", "0"))
    default_automation_enabled: bool = os.getenv("DEFAULT_AGENT_AUTOMATION_ENABLED", "true").lower() == "true"
    portfolio_service_base_url: str = os.getenv("PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8007")
    max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "0.05"))
    kelly_factor: float = float(os.getenv("KELLY_FACTOR", "0.25"))
    min_order_notional: float = float(os.getenv("MIN_ORDER_NOTIONAL", "10.0"))
    default_stop_loss_pct: float = float(os.getenv("DEFAULT_AGENT_STOP_LOSS_PCT", "0.02"))
    default_take_profit_pct: float = float(os.getenv("DEFAULT_AGENT_TAKE_PROFIT_PCT", "0.05"))
    default_trailing_stop_pct: float = float(os.getenv("DEFAULT_AGENT_TRAILING_STOP_PCT", "0.03"))


settings = Settings()
