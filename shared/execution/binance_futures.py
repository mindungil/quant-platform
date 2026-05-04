"""Binance Futures connector implementation.

Supports both mainnet and testnet. Requires API key and secret
for authenticated endpoints (positions, orders, balances).

Testnet URL: https://testnet.binancefuture.com
Mainnet URL: https://fapi.binance.com
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

from shared.execution.connector import ExchangeConnector
from shared.execution.risk_limits import OrderResult


MAINNET_URL = "https://fapi.binance.com"
TESTNET_URL = "https://testnet.binancefuture.com"
SAPI_MAINNET_URL = "https://api.binance.com"
SAPI_TESTNET_URL = "https://testnet.binance.vision"
MAX_RETRIES = 3

# Binance USDT-M futures clientOrderId constraints:
#   - max 36 chars, alphanumeric + . _ ~ -
#   - must be unique per account (24h window enforced by Binance)
# Same client_order_id submitted twice → second call returns the first
# order's metadata as if it succeeded, enabling safe retry on network
# timeouts without double-fill risk.
CLIENT_ORDER_ID_PREFIX = "qx"  # "quant-execution"
CLIENT_ORDER_ID_MAX = 36


def _generate_client_order_id(prefix: str = CLIENT_ORDER_ID_PREFIX) -> str:
    """Random unique id within Binance's 36-char limit."""
    raw = f"{prefix}-{uuid.uuid4().hex}"
    return raw[:CLIENT_ORDER_ID_MAX]


class BinanceFuturesConnector(ExchangeConnector):
    """Binance USDT-M Futures connector."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = TESTNET_URL if testnet else MAINNET_URL
        self.sapi_url = SAPI_TESTNET_URL if testnet else SAPI_MAINNET_URL

    # ----- Permission validation (call before any --live execution) -----

    def validate_permissions(self) -> dict:
        """Verify the API key cannot withdraw or trade non-futures products.

        Calls /sapi/v1/account/apiRestrictions and asserts:
          - enableWithdrawals must be False (NEVER allow withdrawal)
          - enableInternalTransfer should be False (no asset move out)
          - enableFutures must be True (we need it)

        Raises PermissionError on any unsafe permission. Returns the raw
        restrictions dict on success (for logging).

        IMPORTANT: testnet keys often don't expose this endpoint — we skip
        the check on testnet and only enforce on mainnet (testnet=False).
        """
        if self.testnet:
            return {"_skipped": "testnet"}

        try:
            data = self._sapi_signed_get("/sapi/v1/account/apiRestrictions")
        except Exception as exc:
            raise PermissionError(
                f"Cannot read API key permissions (/sapi/v1/account/apiRestrictions): "
                f"{exc}. Refusing to run live execution against an unverified key."
            ) from exc

        if data.get("enableWithdrawals", False):
            raise PermissionError(
                "API key has WITHDRAWAL permission enabled. Disable it in the "
                "Binance API management UI before running --live. The trading "
                "engine never withdraws — leaving this on is a self-custody risk."
            )
        if data.get("enableInternalTransfer", False):
            raise PermissionError(
                "API key has INTERNAL_TRANSFER permission enabled. Disable it — "
                "the engine doesn't transfer between sub-accounts."
            )
        if not data.get("enableFutures", False):
            raise PermissionError(
                "API key does NOT have Futures permission. Enable Futures-only "
                "in the Binance API management UI."
            )
        return data

    # ----- Public API -----

    def get_mark_prices(self, symbols: list[str]) -> dict[str, float]:
        data = self._public_get("/fapi/v1/premiumIndex")
        prices = {}
        for item in data:
            if item["symbol"] in symbols:
                prices[item["symbol"]] = float(item["markPrice"])
        return prices

    # ----- Authenticated API -----

    def get_positions(self) -> dict[str, float]:
        data = self._signed_get("/fapi/v2/positionRisk")
        positions = {}
        for p in data:
            qty = float(p.get("positionAmt", 0))
            if abs(qty) > 1e-10:
                positions[p["symbol"]] = qty
        return positions

    def get_balances(self) -> dict[str, float]:
        data = self._signed_get("/fapi/v2/balance")
        balances = {}
        for b in data:
            free = float(b.get("availableBalance", 0))
            if free > 0:
                balances[b["asset"]] = free
        return balances

    def get_account_equity(self) -> float:
        data = self._signed_get("/fapi/v2/account")
        return float(data.get("totalWalletBalance", 0))

    def place_market_order(
        self, symbol: str, side: str, quantity: float,
        *, client_order_id: str | None = None,
    ) -> OrderResult:
        """Place a market order.

        client_order_id: pass the SAME id when retrying the same logical
        intent. Binance dedupes by newClientOrderId for 24h, so retries
        after a network timeout cannot double-fill. If omitted a random
        UUID is generated (still safe, but not retry-idempotent — caller
        wanting retry safety must persist the id and pass it on retries).
        """
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": f"{quantity:.6f}",
            "newClientOrderId": client_order_id or _generate_client_order_id(),
        }
        return self._place_order(params)

    def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float,
        *, client_order_id: str | None = None,
    ) -> OrderResult:
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "LIMIT",
            "quantity": f"{quantity:.6f}",
            "price": f"{price:.2f}",
            "timeInForce": "GTC",
            "newClientOrderId": client_order_id or _generate_client_order_id(),
        }
        return self._place_order(params)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self._signed_delete("/fapi/v1/order", {
                "symbol": symbol,
                "orderId": order_id,
            })
            return True
        except Exception:
            return False

    # ----- Internal -----

    def _place_order(self, params: dict) -> OrderResult:
        client_order_id = params.get("newClientOrderId", "")
        try:
            data = self._signed_post("/fapi/v1/order", params)
            return OrderResult(
                symbol=data.get("symbol", params.get("symbol", "")),
                side=data.get("side", params.get("side", "")),
                quantity=float(data.get("origQty", 0)),
                filled_quantity=float(data.get("executedQty", 0)),
                avg_price=float(data.get("avgPrice", 0)),
                status=data.get("status", "UNKNOWN"),
                order_id=str(data.get("orderId", "")),
                client_order_id=str(data.get("clientOrderId", client_order_id)),
            )
        except Exception as e:
            return OrderResult(
                symbol=params.get("symbol", ""),
                side=params.get("side", ""),
                quantity=float(params.get("quantity", 0)),
                filled_quantity=0,
                avg_price=0,
                status="ERROR",
                order_id="",
                client_order_id=client_order_id,
                error=str(e),
            )

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = str(int(time.time() * 1000))
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _headers(self) -> dict:
        return {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "quant-engine/1.0",
        }

    def _public_get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "quant-engine/1.0"})
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read())
            except Exception:
                time.sleep(1 * (attempt + 1))
        return []

    def _signed_get(self, path: str, params: dict | None = None) -> Any:
        p = dict(params or {})
        p = self._sign(p)
        query = urllib.parse.urlencode(p)
        url = f"{self.base_url}{path}?{query}"
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _sapi_signed_get(self, path: str, params: dict | None = None) -> Any:
        """Signed GET against the spot SAPI host (used for apiRestrictions)."""
        p = dict(params or {})
        p = self._sign(p)
        query = urllib.parse.urlencode(p)
        url = f"{self.sapi_url}{path}?{query}"
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_post(self, path: str, params: dict) -> Any:
        p = self._sign(dict(params))
        data = urllib.parse.urlencode(p).encode()
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_delete(self, path: str, params: dict) -> Any:
        p = self._sign(dict(params))
        query = urllib.parse.urlencode(p)
        url = f"{self.base_url}{path}?{query}"
        req = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
