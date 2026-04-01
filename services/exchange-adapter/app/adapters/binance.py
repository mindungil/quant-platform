from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
import urllib.request
import json
import threading
from typing import Any

from app.adapters.base import ExchangeAdapter
from app.core.config import settings


class _RateLimiter:
    """Simple sliding-window rate limiter: max *limit* calls per *window_seconds*."""

    def __init__(self, limit: int = 1200, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a request slot is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                # Purge timestamps outside the window
                self._timestamps = [t for t in self._timestamps if now - t < self._window]
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                # Calculate how long to wait for the oldest entry to expire
                wait = self._window - (now - self._timestamps[0])
            time.sleep(max(wait, 0.01))


class BinanceAdapter(ExchangeAdapter):
    """Binance spot adapter using the v3 REST API.

    Rate-limited to 1200 requests / minute (Binance default weight budget).
    """

    def __init__(self) -> None:
        self._base_url = settings.binance_api_base_url.rstrip("/")
        self._rate_limiter = _RateLimiter(limit=1200, window_seconds=60.0)

    @property
    def name(self) -> str:
        return "binance"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sign(query_string: str, secret: str) -> str:
        return hmac.new(
            secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        self._rate_limiter.acquire()

        params = dict(params or {})
        if signed and api_secret:
            params["timestamp"] = str(int(time.time() * 1000))
            qs = urllib.parse.urlencode(params)
            params["signature"] = self._sign(qs, api_secret)

        url = f"{self._base_url}{path}"
        encoded = urllib.parse.urlencode(params) if params else ""

        if method.upper() == "GET" and encoded:
            url = f"{url}?{encoded}"
            data = None
        else:
            data = encoded.encode("utf-8") if encoded else None

        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if api_key:
            req.add_header("X-MBX-APIKEY", api_key)

        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]

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
        params: dict[str, Any] = {
            "symbol": asset,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": str(quantity),
            "newOrderRespType": "FULL",
        }
        raw = self._request(
            "POST",
            "/api/v3/order",
            params,
            api_key=api_key,
            api_secret=api_secret,
            signed=True,
        )
        return {
            "status": raw.get("status", "UNKNOWN"),
            "raw": raw,
        }

    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        try:
            self._request(
                "GET",
                "/api/v3/account",
                api_key=api_key,
                api_secret=api_secret,
                signed=True,
            )
            return True
        except Exception:
            return False
