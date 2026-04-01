from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from app.adapters.base import ExchangeAdapter
from app.core.config import settings


class _RateLimiter:
    """Simple sliding-window rate limiter: max *limit* calls per *window_seconds*."""

    def __init__(self, limit: int = 200, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a request slot is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < self._window]
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                wait = self._window - (now - self._timestamps[0])
            time.sleep(max(wait, 0.01))


class AlpacaAdapter(ExchangeAdapter):
    """Alpaca exchange adapter using the v2 Trading API.

    Rate-limited to 200 requests / minute (Alpaca default).
    """

    def __init__(self) -> None:
        self._base_url = settings.alpaca_api_base_url.rstrip("/")
        self._rate_limiter = _RateLimiter(limit=200, window_seconds=60.0)

    @property
    def name(self) -> str:
        return "alpaca"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_symbol(asset: str) -> str:
        """Strip common suffixes to get a bare ticker for Alpaca.

        Examples:
            "AAPLUSDT" -> "AAPL"   (not useful, but defensive)
            "AAPL"     -> "AAPL"
        """
        # Alpaca uses plain ticker symbols; strip USDT/USD/KRW if present
        import re
        return re.sub(r"(USDT|USD|KRW)$", "", asset.upper())

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        body_json: dict[str, Any] | None = None,
    ) -> Any:
        self._rate_limiter.acquire()

        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {}

        if api_key:
            headers["APCA-API-KEY-ID"] = api_key
        if api_secret:
            headers["APCA-API-SECRET-KEY"] = api_secret

        data: bytes | None = None
        if method.upper() == "GET" and params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        elif body_json:
            data = json.dumps(body_json).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method.upper())
        for k, v in headers.items():
            req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

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
        symbol = self._normalize_symbol(asset)
        body: dict[str, Any] = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side.lower(),
            "type": "market",
            "time_in_force": "day",
        }
        raw = self._request(
            "POST",
            "/v2/orders",
            api_key=api_key,
            api_secret=api_secret,
            body_json=body,
        )
        status = raw.get("status", "UNKNOWN").upper()
        # Alpaca returns statuses like "accepted", "new", "filled" etc.
        return {
            "status": status,
            "raw": raw,
        }

    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        try:
            self._request(
                "GET",
                "/v2/account",
                api_key=api_key,
                api_secret=api_secret,
            )
            return True
        except Exception:
            return False

    def cancel_order(
        self,
        *,
        order_id: str,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        try:
            self._request(
                "DELETE",
                f"/v2/orders/{order_id}",
                api_key=api_key,
                api_secret=api_secret,
            )
            # Alpaca DELETE returns 204 with no body on success;
            # if we get here without error, the cancel succeeded.
            return {
                "status": "CANCELED",
                "raw": {},
            }
        except urllib.request.HTTPError as e:
            # Try to parse error body if present
            body = {}
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                pass
            raise Exception(f"Alpaca cancel_order failed: {e.code} {body}") from e

    def get_balance(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        raw = self._request(
            "GET",
            "/v2/account",
            api_key=api_key,
            api_secret=api_secret,
        )
        balances = [
            {"asset": "USD", "free": raw.get("buying_power", "0"), "locked": "0"},
            {"asset": "CASH", "free": raw.get("cash", "0"), "locked": "0"},
            {"asset": "PORTFOLIO", "free": raw.get("portfolio_value", "0"), "locked": "0"},
        ]
        return {
            "balances": balances,
            "raw": raw,
        }

    def get_positions(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        raw = self._request(
            "GET",
            "/v2/positions",
            api_key=api_key,
            api_secret=api_secret,
        )
        positions = [
            {
                "symbol": p.get("symbol"),
                "orderId": p.get("asset_id"),
                "side": p.get("side"),
                "type": "position",
                "quantity": p.get("qty"),
                "price": p.get("avg_entry_price"),
                "status": "OPEN",
            }
            for p in (raw if isinstance(raw, list) else [])
        ]
        return {
            "positions": positions,
            "raw": raw,
        }

    def get_orderbook(
        self,
        *,
        asset: str,
        exchange: str,
        depth: int = 20,
    ) -> dict[str, Any]:
        # Alpaca does not provide a public REST orderbook endpoint.
        # Return empty bids/asks with a note in raw.
        return {
            "bids": [],
            "asks": [],
            "raw": {"note": "Alpaca does not provide a public REST orderbook endpoint."},
        }
