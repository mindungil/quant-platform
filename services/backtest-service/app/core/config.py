import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    market_data_base_url: str = os.getenv(
        "MARKET_DATA_BASE_URL", "http://localhost:8001"
    )
    # Backtest defaults
    entry_threshold: float = float(os.getenv("BACKTEST_ENTRY_THRESHOLD", "0.3"))
    exit_threshold: float = float(os.getenv("BACKTEST_EXIT_THRESHOLD", "0.3"))
    stop_loss_pct: float = float(os.getenv("BACKTEST_STOP_LOSS_PCT", "0.05"))
    initial_capital: float = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "10000"))


settings = Settings()
