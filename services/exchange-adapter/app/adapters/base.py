from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ExchangeAdapter(ABC):
    """Abstract interface that every exchange adapter must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical lowercase exchange name (e.g. 'binance')."""

    @abstractmethod
    def place_order(
        self,
        *,
        asset: str,
        side: str,
        quantity: float,
        notional: float,
        api_key: str | None = None,
        api_secret: str | None = None,
        sandbox: bool = True,
    ) -> dict[str, Any]:
        """Send an order to the exchange and return the raw response dict.

        Returns a dict with at least:
            - status: str  (e.g. "FILLED", "REJECTED", ...)
            - raw: dict    (full exchange response for auditing)
        """

    @abstractmethod
    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        """Return True if the given credentials are accepted by the exchange."""

    @abstractmethod
    def cancel_order(
        self,
        *,
        order_id: str,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an open order on the exchange.

        Returns a dict with at least:
            - status: str  (e.g. "CANCELED", "REJECTED", ...)
            - raw: dict    (full exchange response for auditing)
        """

    @abstractmethod
    def get_balance(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        """Fetch account balance from the exchange.

        Returns a dict with at least:
            - balances: list[dict]  (each with asset, free, locked)
            - raw: dict
        """

    @abstractmethod
    def get_positions(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        """Fetch open positions / open orders from the exchange.

        Returns a dict with at least:
            - positions: list[dict]
            - raw: dict | list
        """

    @abstractmethod
    def get_orderbook(
        self,
        *,
        asset: str,
        exchange: str,
        depth: int = 20,
    ) -> dict[str, Any]:
        """Fetch the order book (depth) for a given asset.

        Returns a dict with at least:
            - bids: list[list]  (each [price, qty])
            - asks: list[list]
            - raw: dict
        """
