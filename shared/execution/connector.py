"""Abstract exchange connector interface.

All exchange-specific implementations subclass ExchangeConnector.
This allows swapping exchanges without changing trading logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from shared.execution.risk_limits import OrderResult


class ExchangeConnector(ABC):
    """Abstract interface for exchange interactions."""

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Return current positions: {symbol: signed_quantity}.
        Positive = long, negative = short.
        """

    @abstractmethod
    def get_balances(self) -> dict[str, float]:
        """Return available balances: {asset: free_balance}."""

    @abstractmethod
    def get_mark_prices(self, symbols: list[str]) -> dict[str, float]:
        """Return current mark prices: {symbol: price}."""

    @abstractmethod
    def place_market_order(
        self, symbol: str, side: str, quantity: float,
    ) -> OrderResult:
        """Place a market order. Returns fill result."""

    @abstractmethod
    def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float,
    ) -> OrderResult:
        """Place a limit order. Returns immediately (may not be filled)."""

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an open order. Returns True if successfully cancelled."""

    @abstractmethod
    def get_account_equity(self) -> float:
        """Return total account equity in USD."""
