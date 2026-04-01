from __future__ import annotations

from typing import Any

from app.adapters.base import ExchangeAdapter


class UpbitAdapter(ExchangeAdapter):
    """Upbit exchange adapter stub.

    Not yet implemented -- all methods raise NotImplementedError so callers
    know this adapter is a placeholder.
    """

    @property
    def name(self) -> str:
        return "upbit"

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
        raise NotImplementedError("Upbit adapter is not yet implemented")

    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        raise NotImplementedError("Upbit adapter is not yet implemented")

    def cancel_order(
        self,
        *,
        order_id: str,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Upbit adapter is not yet implemented")

    def get_balance(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Upbit adapter is not yet implemented")

    def get_positions(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError("Upbit adapter is not yet implemented")

    def get_orderbook(
        self,
        *,
        asset: str,
        exchange: str,
        depth: int = 20,
    ) -> dict[str, Any]:
        raise NotImplementedError("Upbit adapter is not yet implemented")
