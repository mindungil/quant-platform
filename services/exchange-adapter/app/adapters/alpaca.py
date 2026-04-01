from __future__ import annotations

from typing import Any

from app.adapters.base import ExchangeAdapter


class AlpacaAdapter(ExchangeAdapter):
    """Alpaca exchange adapter stub.

    Not yet implemented -- all methods raise NotImplementedError so callers
    know this adapter is a placeholder.
    """

    @property
    def name(self) -> str:
        return "alpaca"

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
        raise NotImplementedError("Alpaca adapter is not yet implemented")

    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        raise NotImplementedError("Alpaca adapter is not yet implemented")
