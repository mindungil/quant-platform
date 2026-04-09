from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

from app.adapters.base import ExchangeAdapter


class _RateLimiter:
    """Bithumb is generous (~135 req/sec) but we keep 30 req/sec for safety."""

    def __init__(self, limit: int = 30, window_seconds: float = 1.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < self._window]
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                wait = self._window - (now - self._timestamps[0])
            time.sleep(max(wait, 0.01))


class BithumbAdapter(ExchangeAdapter):
    """Bithumb exchange adapter using v2 REST API.

    Korean second-largest exchange. KRW pairs only (no USDT pairs natively).
    Authentication uses HMAC-SHA512 (unlike Upbit's JWT/HS256).
    """

    BASE_URL = "https://api.bithumb.com"

    def __init__(self) -> None:
        self._rate_limiter = _RateLimiter(limit=30, window_seconds=1.0)

    @property
    def name(self) -> str:
        return "bithumb"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_bithumb_pair(asset: str) -> tuple[str, str]:
        """Convert asset symbol to (order_currency, payment_currency).

        Examples:
            "BTC"     -> ("BTC", "KRW")
            "BTCKRW"  -> ("BTC", "KRW")
            "BTCUSDT" -> ("BTC", "KRW")  (Bithumb is KRW-only)
            "KRW-BTC" -> ("BTC", "KRW")
        """
        if "-" in asset:
            payment, order = asset.split("-", 1)
            return order.upper(), payment.upper()
        cleaned = re.sub(r"(KRW|USDT|USD)$", "", asset.upper())
        return cleaned, "KRW"

    def _public_request(self, path: str) -> Any:
        self._rate_limiter.acquire()
        url = f"{self.BASE_URL}{path}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data

    def _private_request(
        self,
        endpoint: str,
        params: dict[str, Any],
        api_key: str,
        api_secret: str,
    ) -> Any:
        self._rate_limiter.acquire()

        nonce = str(int(time.time() * 1000000))
        params["endpoint"] = endpoint

        # URL-encoded payload
        payload = urllib.parse.urlencode(params)

        # Signature: endpoint + chr(0) + payload + chr(0) + nonce
        signing_data = endpoint + chr(0) + payload + chr(0) + nonce
        signature_hex = hmac.new(
            api_secret.encode("utf-8"),
            signing_data.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()
        signature_b64 = base64.b64encode(signature_hex.encode("utf-8")).decode("utf-8")

        url = f"{self.BASE_URL}{endpoint}"
        req = urllib.request.Request(
            url,
            data=payload.encode("utf-8"),
            method="POST",
        )
        req.add_header("Api-Key", api_key)
        req.add_header("Api-Sign", signature_b64)
        req.add_header("Api-Nonce", nonce)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data

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
        order_currency, payment_currency = self._to_bithumb_pair(asset)
        bithumb_type = "bid" if side.upper() == "BUY" else "ask"

        params: dict[str, Any] = {
            "order_currency": order_currency,
            "payment_currency": payment_currency,
            "units": str(quantity),
            "type": bithumb_type,
        }

        try:
            raw = self._private_request(
                "/trade/market_buy" if bithumb_type == "bid" else "/trade/market_sell",
                params,
                api_key=api_key or "",
                api_secret=api_secret or "",
            )
        except Exception as e:
            return {"status": "REJECTED", "raw": {"error": str(e)}}

        return {
            "status": raw.get("status", "UNKNOWN"),
            "raw": raw,
        }

    def validate_credentials(self, api_key: str, api_secret: str) -> bool:
        try:
            result = self._private_request(
                "/info/account",
                {"order_currency": "BTC", "payment_currency": "KRW"},
                api_key=api_key,
                api_secret=api_secret,
            )
            return isinstance(result, dict) and result.get("status") == "0000"
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
        # Bithumb cancel requires order_id, type, currency
        params = {
            "order_id": order_id,
            "type": "bid",  # default; production should track actual side
            "order_currency": "BTC",
            "payment_currency": "KRW",
        }
        try:
            raw = self._private_request(
                "/trade/cancel",
                params,
                api_key=api_key or "",
                api_secret=api_secret or "",
            )
        except Exception as e:
            return {"status": "REJECTED", "raw": {"error": str(e)}}

        return {
            "status": raw.get("status", "UNKNOWN"),
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
        if not api_key or not api_secret:
            return {"balances": [], "raw": {"error": "missing_credentials"}}

        try:
            raw = self._private_request(
                "/info/balance",
                {"currency": "ALL"},
                api_key=api_key,
                api_secret=api_secret,
            )
        except Exception as e:
            return {"balances": [], "raw": {"error": str(e)}}

        balances: list[dict[str, str]] = []
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        # Bithumb returns: total_btc, available_btc, in_use_btc, etc.
        seen: set[str] = set()
        if isinstance(data, dict):
            for key, value in data.items():
                if key.startswith("total_"):
                    currency = key.replace("total_", "").upper()
                    if currency in seen:
                        continue
                    seen.add(currency)
                    try:
                        total = float(value)
                        in_use = float(data.get(f"in_use_{currency.lower()}", 0))
                        if total > 0 or in_use > 0:
                            balances.append({
                                "asset": currency,
                                "free": str(total - in_use),
                                "locked": str(in_use),
                            })
                    except (ValueError, TypeError):
                        pass

        return {"balances": balances, "raw": raw}

    def get_positions(
        self,
        *,
        user_id: str,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict[str, Any]:
        # Bithumb spot only — no positions concept; return open orders
        if not api_key or not api_secret:
            return {"positions": [], "raw": {"error": "missing_credentials"}}

        try:
            raw = self._private_request(
                "/info/orders",
                {"order_currency": "BTC", "payment_currency": "KRW"},
                api_key=api_key,
                api_secret=api_secret,
            )
        except Exception as e:
            return {"positions": [], "raw": {"error": str(e)}}

        positions: list[dict[str, Any]] = []
        data = raw.get("data", []) if isinstance(raw, dict) else []
        if isinstance(data, list):
            for o in data:
                if not isinstance(o, dict):
                    continue
                positions.append({
                    "symbol": f"{o.get('order_currency', 'BTC')}/{o.get('payment_currency', 'KRW')}",
                    "orderId": o.get("order_id"),
                    "side": o.get("type"),
                    "type": "limit",
                    "quantity": o.get("units"),
                    "price": o.get("price"),
                    "status": o.get("order_status"),
                })

        return {"positions": positions, "raw": raw}

    def get_orderbook(
        self,
        *,
        asset: str,
        exchange: str,
        depth: int = 20,
    ) -> dict[str, Any]:
        order_currency, payment_currency = self._to_bithumb_pair(asset)
        try:
            raw = self._public_request(
                f"/public/orderbook/{order_currency}_{payment_currency}"
            )
        except Exception as e:
            return {"bids": [], "asks": [], "raw": {"error": str(e)}}

        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        bids_raw = data.get("bids", []) if isinstance(data, dict) else []
        asks_raw = data.get("asks", []) if isinstance(data, dict) else []

        bids = [
            [str(b.get("price", 0)), str(b.get("quantity", 0))]
            for b in bids_raw[:depth]
            if isinstance(b, dict)
        ]
        asks = [
            [str(a.get("price", 0)), str(a.get("quantity", 0))]
            for a in asks_raw[:depth]
            if isinstance(a, dict)
        ]

        return {"bids": bids, "asks": asks, "raw": raw}
