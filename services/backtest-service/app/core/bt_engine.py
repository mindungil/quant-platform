"""Backtrader-based backtesting engine.

Provides a more robust alternative to the custom evaluator, with proper
order execution simulation, broker modeling, and trade logging.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import backtrader as bt

    class SignalStrategy(bt.Strategy):
        """Strategy that trades based on pre-computed signal scores."""

        params = (
            ("entry_threshold", 0.3),
            ("exit_threshold", 0.3),
            ("stop_loss_pct", 0.05),
            ("take_profit_pct", 0.10),
            ("trailing_stop_pct", 0.03),
            ("scores", None),  # pre-computed signal scores array
        )

        def __init__(self):
            self.trades_log = []
            self.order = None
            self.entry_price = None
            self.highest_since_entry = None

        def next(self):
            if self.p.scores is None:
                return

            idx = len(self) - 1
            if idx >= len(self.p.scores):
                return

            score = self.p.scores[idx]
            price = self.data.close[0]

            # If we have a position
            if self.position:
                pnl_pct = (price - self.entry_price) / self.entry_price if self.entry_price else 0

                if self.position.size > 0:  # Long position
                    self.highest_since_entry = max(self.highest_since_entry or price, price)

                    should_exit = False
                    reason = ""
                    if pnl_pct >= self.p.take_profit_pct:
                        should_exit, reason = True, "take_profit"
                    elif pnl_pct <= -self.p.stop_loss_pct:
                        should_exit, reason = True, "stop_loss"
                    elif self.p.trailing_stop_pct > 0 and self.highest_since_entry:
                        trail_trigger = self.highest_since_entry * (1 - self.p.trailing_stop_pct)
                        if price <= trail_trigger:
                            should_exit, reason = True, "trailing_stop"
                    elif score < -self.p.exit_threshold:
                        should_exit, reason = True, "signal_reversal"

                    if should_exit:
                        self.close()
                        self.trades_log.append({
                            "entry_price": self.entry_price,
                            "exit_price": price,
                            "pnl_pct": pnl_pct,
                            "side": "BUY",
                            "reason": reason,
                        })

                elif self.position.size < 0:  # Short position
                    pnl_pct = (self.entry_price - price) / self.entry_price if self.entry_price else 0
                    should_exit = False
                    reason = ""
                    if pnl_pct >= self.p.take_profit_pct:
                        should_exit, reason = True, "take_profit"
                    elif pnl_pct <= -self.p.stop_loss_pct:
                        should_exit, reason = True, "stop_loss"
                    elif score > self.p.exit_threshold:
                        should_exit, reason = True, "signal_reversal"

                    if should_exit:
                        self.close()
                        self.trades_log.append({
                            "entry_price": self.entry_price,
                            "exit_price": price,
                            "pnl_pct": pnl_pct,
                            "side": "SELL",
                            "reason": reason,
                        })

            else:
                # No position — check entry signals
                if score > self.p.entry_threshold:
                    self.buy()
                    self.entry_price = price
                    self.highest_since_entry = price
                elif score < -self.p.entry_threshold:
                    self.sell()
                    self.entry_price = price
                    self.highest_since_entry = price

    BACKTRADER_AVAILABLE = True

except ImportError:
    BACKTRADER_AVAILABLE = False
    logger.warning("backtrader not available, bt_engine disabled")


def run_backtrader_backtest(
    df: pd.DataFrame,
    scores: np.ndarray,
    initial_capital: float = 10000.0,
    commission_pct: float = 0.001,
    entry_threshold: float = 0.3,
    exit_threshold: float = 0.3,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.10,
    trailing_stop_pct: float = 0.03,
) -> dict:
    """Run a backtest using Backtrader framework.

    Args:
        df: OHLCV DataFrame with columns: timestamp, open, high, low, close, volume
        scores: Pre-computed signal scores (same length as df)
        initial_capital: Starting portfolio value
        commission_pct: Commission per trade (0.001 = 0.1%)

    Returns:
        Dict with trades, metrics, and final portfolio value.
    """
    if not BACKTRADER_AVAILABLE:
        return {"error": "backtrader_not_available"}

    cerebro = bt.Cerebro()

    # Create data feed from DataFrame
    data_df = df.copy()
    data_df.index = pd.to_datetime(data_df["timestamp"])
    data_df = data_df[["open", "high", "low", "close", "volume"]]
    data_feed = bt.feeds.PandasData(dataname=data_df)
    cerebro.adddata(data_feed)

    # Add strategy with pre-computed scores
    cerebro.addstrategy(
        SignalStrategy,
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        scores=scores.tolist(),
    )

    # Broker settings
    cerebro.broker.setcash(initial_capital)
    cerebro.broker.setcommission(commission=commission_pct)

    # Run
    results = cerebro.run()
    strategy = results[0]

    # Extract results
    final_value = cerebro.broker.getvalue()
    total_return = (final_value - initial_capital) / initial_capital
    trades = strategy.trades_log

    return {
        "engine": "backtrader",
        "initial_capital": initial_capital,
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 4),
        "trade_count": len(trades),
        "trades": trades,
        "commission_pct": commission_pct,
    }
