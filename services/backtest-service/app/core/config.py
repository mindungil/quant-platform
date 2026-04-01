import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    market_data_base_url: str = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")
    # Backtest defaults
    entry_threshold: float = float(os.getenv("BACKTEST_ENTRY_THRESHOLD", "0.3"))
    exit_threshold: float = float(os.getenv("BACKTEST_EXIT_THRESHOLD", "0.3"))
    stop_loss_pct: float = float(os.getenv("BACKTEST_STOP_LOSS_PCT", "0.05"))
    take_profit_pct: float = float(os.getenv("BACKTEST_TAKE_PROFIT_PCT", "0.10"))
    trailing_stop_pct: float = float(os.getenv("BACKTEST_TRAILING_STOP_PCT", "0.03"))
    initial_capital: float = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "10000"))
    # Transaction costs (in basis points)
    slippage_bps: float = float(os.getenv("BACKTEST_SLIPPAGE_BPS", "5"))
    commission_bps: float = float(os.getenv("BACKTEST_COMMISSION_BPS", "10"))
    # Walk-forward
    walk_forward_windows: int = int(os.getenv("BACKTEST_WALK_FORWARD_WINDOWS", "3"))
    train_ratio: float = float(os.getenv("BACKTEST_TRAIN_RATIO", "0.7"))
    # Risk-free rate for Sharpe
    risk_free_rate_annual: float = float(os.getenv("RISK_FREE_RATE_ANNUAL", "0.05"))


settings = Settings()
