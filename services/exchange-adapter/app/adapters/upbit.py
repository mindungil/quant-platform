from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid as _uuid
from typing import Any

import jwt

from app.adapters.base import ExchangeAdapter
from app.core.config import settings


class _RateLimiter:
    """Simple sliding-window rate limiter: max *limit* calls per *window_seconds*."""

    def __init__(self, limit: int = 10, window_seconds: float = 1.0) -> None:
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


class UpbitAdapter(ExchangeAdapter):
    """Upbit exchange adapter using the v1 REST API.

    Rate-limited to 10 requests / second (Upbit default).
    """

    def __init__(self) -> None:
        self._base_url = settings.upbit_api_base_url.rstrip("/")
        self._rate_limiter = _RateLimiter(limit=10, window_seconds=1.0)

    @property
    def name(self) -> str:
        return "upbit"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_upbit_market(asset: str) -> str:
        """Convert generic asset symbol to Upbit market code.

        Examples:
            "BTCUSDT" -> "KRW-BTC"
            "ETHKRW"  -> "KRW-ETH"
            "BTC"     -> "KRW-BTC"
            "KRW-BTC" -> "KRW-BTC"  (passthrough)
        """
        if "-" in asset:
            return asset
        cleaned = re.sub(r"(USDT|KRW|USD)$", "", asset.upper())
        return f"KRW-{cleaned}"

    @staticmethod
    def _make_jwt(api_key: str, api_secret: str, query_params: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {
            "access_key": api_key,
            "nonce": str(_uuid.uuid4()),
        }
        if query_params:
            query_string = urllib.parse.urlencode(query_params)
            query_hash = hashlib.sha512(query_string.encode("utf-8")).hexdigest()
            payload["query_hash"] = query_hash
            payload["query_hash_alg"] = "SHA512"
        return jwt.encode(payload, api_secret, algorithm="HS256")

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        signed: bool = False,
        body_params: dict[str, Any] | None = None,
    ) -> Any:
        self._rate_limiter.acquire()

        url = f"{self._base_url}{path}"
        headers: dict[str, str] = {}

        if signed and api_key and api_secret:
            # For GET/DELETE with query params, hash the query string
            auth_params = params if method.upper() in ("GET", "DELETE") and params else body_params
            token = self._make_jwt(api_key, api_secret, auth_params)
            headers["Authorization"] = f"Bearer {token}"

        data: bytes | None = None
        if method.upper() in ("GET", "DELETE") and params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        elif body_params:
            data = json.dumps(body_params).encode("utf-8")
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
        market = self._to_upbit_market(asset)
        upbit_side = "bid" if side.upper() == "BUY" else "ask"

        body: dict[str, Any] = {
            "market": market,
            "side": upbit_side,
            "ord_type": "market",
        }
        # Upbit market orders: bid requires price (KRW amount), ask requires volume
        if upbit_side == "bid":
            body["price"] = str(notional)
        else:
            body["volume"] = str(quantity)

        raw = self._request(
            "POST",
            "/v1/orders",
            api_key=api_key,
            api_secret=api_secret,
            signed=True,
            body_params=body,
        )
        return {
            "status": raw.get("state", "UNKNOWN").upper(),
            "raw": raw,
        }

    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        try:
            self._request(
                "GET",
                "/v1/api_keys",
                api_key=api_key,
                api_secret=api_secret,
                signed=True,
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
        params = {"uuid": order_id}
        raw = self._request(
            "DELETE",
            "/v1/order",
            params,
            api_key=api_key,
            api_secret=api_secret,
            signed=True,
        )
        return {
            "status": raw.get("state", "CANCELED").upper(),
            "raw": raw,
        }

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
            "/v1/accounts",
            api_key=api_key,
            api_secret=api_secret,
            signed=True,
        )
        balances = [
            {
                "asset": acct.get("currency", ""),
                "free": acct.get("balance", "0"),
                "locked": acct.get("locked", "0"),
            }
            for acct in (raw if isinstance(raw, list) else [])
            if float(acct.get("balance", 0)) > 0 or float(acct.get("locked", 0)) > 0
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
        params = {"state": "wait"}
        raw = self._request(
            "GET",
            "/v1/orders",
            params,
            api_key=api_key,
            api_secret=api_secret,
            signed=True,
        )
        positions = [
            {
                "symbol": o.get("market"),
                "orderId": o.get("uuid"),
                "side": o.get("side"),
                "type": o.get("ord_type"),
                "quantity": o.get("volume"),
                "price": o.get("price"),
                "status": o.get("state"),
            }
            for o in (raw if isinstance(raw, list) else [])
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
        market = self._to_upbit_market(asset)
        raw = self._request(
            "GET",
            "/v1/orderbook",
            {"markets": market},
        )
        # Upbit returns a list of orderbooks; pick the first one
        book = raw[0] if isinstance(raw, list) and raw else {}
        units = book.get("orderbook_units", [])

        bids = [[str(u.get("bid_price", 0)), str(u.get("bid_size", 0))] for u in units[:depth]]
        asks = [[str(u.get("ask_price", 0)), str(u.get("ask_size", 0))] for u in units[:depth]]
        return {
            "bids": bids,
            "asks": asks,
            "raw": raw,
        }
