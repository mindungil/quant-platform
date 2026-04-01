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
