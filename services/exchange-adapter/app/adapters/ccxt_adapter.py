"""Unified ccxt-based exchange adapter.

Provides a standard interface for all exchanges using the ccxt library.
Falls back to existing direct HTTP adapters if ccxt initialization fails.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("exchange-adapter")

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False
    logger.warning("ccxt not installed, using direct HTTP adapters")


class CcxtAdapter:
    """Unified exchange adapter using ccxt."""

    def __init__(self, exchange_id: str, api_key: str = "", api_secret: str = "", sandbox: bool = True):
        if not CCXT_AVAILABLE:
            raise ImportError("ccxt is not available")

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Exchange '{exchange_id}' not supported by ccxt")

        self._exchange = exchange_class({
            "apiKey": api_key,
            "secret": api_secret,
            "sandbox": sandbox,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        self._exchange_id = exchange_id

        if sandbox:
            try:
                self._exchange.set_sandbox_mode(True)
            except Exception:
                pass

        logger.info(f"ccxt adapter initialized: {exchange_id} (sandbox={sandbox})")

    def get_ticker(self, symbol: str) -> dict:
        """Get current ticker for a symbol."""
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            return {
                "symbol": ticker["symbol"],
                "last": ticker["last"],
                "bid": ticker["bid"],
                "ask": ticker["ask"],
                "volume": ticker["baseVolume"],
                "timestamp": ticker["timestamp"],
            }
        except Exception as e:
            logger.error(f"get_ticker failed: {e}")
            return {"error": str(e)}

    def get_balance(self) -> dict:
        """Get account balance."""
        try:
            balance = self._exchange.fetch_balance()
            return {
                "total": balance.get("total", {}),
                "free": balance.get("free", {}),
                "used": balance.get("used", {}),
            }
        except Exception as e:
            logger.error(f"get_balance failed: {e}")
            return {"error": str(e)}

    def place_order(
        self, symbol: str, side: str, amount: float,
        order_type: str = "market", price: float | None = None,
    ) -> dict:
        """Place an order."""
        try:
            order = self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side.lower(),
                amount=amount,
                price=price,
            )
            return {
                "order_id": order["id"],
                "symbol": order["symbol"],
                "side": order["side"],
                "type": order["type"],
                "amount": order["amount"],
                "price": order.get("price"),
                "status": order["status"],
                "filled": order.get("filled", 0),
                "cost": order.get("cost", 0),
                "timestamp": order["timestamp"],
            }
        except Exception as e:
            logger.error(f"place_order failed: {e}")
            return {"error": str(e)}

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an order."""
        try:
            self._exchange.cancel_order(order_id, symbol)
            return {"order_id": order_id, "status": "cancelled"}
        except Exception as e:
            logger.error(f"cancel_order failed: {e}")
            return {"error": str(e)}

    def get_positions(self, symbol: str | None = None) -> list[dict]:
        """Get open positions."""
        try:
            if hasattr(self._exchange, 'fetch_positions'):
                positions = self._exchange.fetch_positions([symbol] if symbol else None)
                return [
                    {
                        "symbol": p["symbol"],
                        "side": p.get("side"),
                        "amount": p.get("contracts", p.get("amount", 0)),
                        "entry_price": p.get("entryPrice", 0),
                        "unrealized_pnl": p.get("unrealizedPnl", 0),
                    }
                    for p in positions
                ]
            return []
        except Exception as e:
            logger.error(f"get_positions failed: {e}")
            return []

    def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> list[dict]:
        """Get OHLCV candle data."""
        try:
            candles = self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return [
                {
                    "timestamp": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in candles
            ]
        except Exception as e:
            logger.error(f"get_ohlcv failed: {e}")
            return []
