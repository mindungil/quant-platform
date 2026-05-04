"""Upbit 현물 connector (KRW 마켓).

Binance Futures connector의 Upbit 현물 버전. 주요 차이점:
- 숏 불가: SELL은 보유 수량 이내에서만 가능
- 마켓 코드: BTCUSDT → KRW-BTC 변환
- 인증: JWT (HS256, query_hash SHA512)
- 수수료: 시장가 0.05%, 지정가 0.05% (각 방향)
- 최소 주문: 5,000 KRW
- Rate limit: 10 req/s (주문 계정 API)

Upbit은 testnet/sandbox 공식 제공 안함 → dry_run 모드로 검증 후 실계좌로 바로.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.parse
import urllib.request
import uuid as _uuid
from typing import Any

import jwt

from shared.execution.connector import ExchangeConnector
from shared.execution.risk_limits import OrderResult


MAINNET_URL = "https://api.upbit.com"
MIN_ORDER_KRW = 5_000.0  # Upbit 최소 주문 금액
MAKER_FEE_BPS = 5.0      # 0.05%
TAKER_FEE_BPS = 5.0      # 0.05% (시장가 동일)


class _RateLimiter:
    """Sliding-window rate limiter: max `limit` calls per `window` seconds."""

    def __init__(self, limit: int = 8, window: float = 1.0) -> None:
        self._limit = limit
        self._window = window
        self._ts: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._ts = [t for t in self._ts if now - t < self._window]
                if len(self._ts) < self._limit:
                    self._ts.append(now)
                    return
                wait = self._window - (now - self._ts[0])
            time.sleep(max(wait, 0.01))


def _to_market(symbol: str) -> str:
    """BTCUSDT/BTC/KRW-BTC → KRW-BTC 로 정규화."""
    if "-" in symbol:
        return symbol
    s = symbol.upper()
    for suffix in ("USDT", "USD", "KRW"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return f"KRW-{s}"


def _from_market(market: str) -> str:
    """KRW-BTC → BTCUSDT 로 역변환 (내부 symbol convention 유지)."""
    if "-" not in market:
        return market
    _, asset = market.split("-", 1)
    return f"{asset}USDT"


class UpbitConnector(ExchangeConnector):
    """Upbit 현물 connector.

    주의: Upbit은 현물이므로 숏 불가. `place_market_order`/`place_limit_order`가
    SELL을 요청받으면 현재 보유량을 초과할 수 없음.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = MAINNET_URL,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._rl = _RateLimiter(limit=8, window=1.0)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _jwt(self, query: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {
            "access_key": self.api_key,
            "nonce": str(_uuid.uuid4()),
        }
        if query:
            qs = urllib.parse.urlencode(query)
            payload["query_hash"] = hashlib.sha512(qs.encode()).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        return jwt.encode(payload, self.api_secret, algorithm="HS256")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        self._rl.acquire()
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}

        if signed:
            auth_q = params if method.upper() in ("GET", "DELETE") and params else body
            headers["Authorization"] = f"Bearer {self._jwt(auth_q)}"

        data: bytes | None = None
        if method.upper() in ("GET", "DELETE") and params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        elif body:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method.upper())
        for k, v in headers.items():
            req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def get_mark_prices(self, symbols: list[str]) -> dict[str, float]:
        """현재가 조회. Upbit ticker는 KRW 가격."""
        markets = [_to_market(s) for s in symbols]
        data = self._request("GET", "/v1/ticker", params={"markets": ",".join(markets)})
        prices: dict[str, float] = {}
        for item in data:
            internal = _from_market(item["market"])
            prices[internal] = float(item["trade_price"])
        return prices

    # ------------------------------------------------------------------
    # Authenticated
    # ------------------------------------------------------------------

    def get_balances(self) -> dict[str, float]:
        """자산별 free balance. KRW 포함 (현금)."""
        data = self._request("GET", "/v1/accounts", signed=True)
        out: dict[str, float] = {}
        for b in data:
            currency = b["currency"]
            free = float(b.get("balance", 0))
            if free > 0:
                out[currency] = free
        return out

    def get_positions(self) -> dict[str, float]:
        """보유 코인 → {symbol: qty}. KRW는 현금이므로 제외."""
        balances = self.get_balances()
        positions: dict[str, float] = {}
        for asset, qty in balances.items():
            if asset == "KRW":
                continue
            positions[f"{asset}USDT"] = qty  # 내부 convention
        return positions

    def get_account_equity(self) -> float:
        """총 자산가치 (KRW 기준).

        각 보유 코인을 현재가로 KRW 환산 + 현금 KRW.
        """
        balances = self.get_balances()
        equity = float(balances.get("KRW", 0))

        coins = [a for a in balances if a != "KRW"]
        if coins:
            symbols = [f"{c}USDT" for c in coins]
            prices = self.get_mark_prices(symbols)
            for asset in coins:
                qty = balances[asset]
                price = prices.get(f"{asset}USDT", 0)
                equity += qty * price
        return equity

    def place_market_order(
        self, symbol: str, side: str, quantity: float,
    ) -> OrderResult:
        """시장가 주문.

        Upbit 현물 규칙:
        - BUY: 'price' 파라미터 = KRW 주문 금액 (수량 아닌 금액 지정)
        - SELL: 'volume' 파라미터 = 코인 수량
        - SELL 수량은 보유량 이내 (숏 불가)
        """
        market = _to_market(symbol)
        side_upper = side.upper()

        if side_upper == "BUY":
            # BUY는 현재가로 notional 계산 필요
            prices = self.get_mark_prices([symbol])
            price = prices.get(symbol, 0)
            if price <= 0:
                return OrderResult(
                    symbol=symbol, side=side_upper, quantity=quantity,
                    filled_quantity=0.0, avg_price=0.0,
                    status="REJECTED", error=f"no_price_for_{symbol}",
                )
            notional_krw = quantity * price
            if notional_krw < MIN_ORDER_KRW:
                return OrderResult(
                    symbol=symbol, side=side_upper, quantity=quantity,
                    filled_quantity=0.0, avg_price=0.0,
                    status="REJECTED",
                    error=f"below_min_order_{notional_krw:.0f}_<_5000_KRW",
                )
            body = {
                "market": market,
                "side": "bid",
                "ord_type": "price",
                "price": str(round(notional_krw, 0)),  # KRW 정수
            }
        else:
            # SELL: 보유 수량 확인
            balances = self.get_balances()
            asset = market.split("-", 1)[1]
            held = balances.get(asset, 0)
            if quantity > held:
                quantity = held  # 보유량으로 clip
            if quantity <= 0:
                return OrderResult(
                    symbol=symbol, side=side_upper, quantity=0.0,
                    filled_quantity=0.0, avg_price=0.0,
                    status="REJECTED", error="no_holdings_to_sell",
                )
            body = {
                "market": market,
                "side": "ask",
                "ord_type": "market",
                "volume": f"{quantity:.8f}",
            }

        try:
            raw = self._request("POST", "/v1/orders", body=body, signed=True)
        except Exception as e:
            return OrderResult(
                symbol=symbol, side=side_upper, quantity=quantity,
                filled_quantity=0.0, avg_price=0.0,
                status="ERROR", error=str(e)[:200],
            )

        return OrderResult(
            symbol=symbol,
            side=side_upper,
            quantity=quantity,
            filled_quantity=float(raw.get("executed_volume", quantity)),
            avg_price=float(raw.get("avg_price", 0)),
            status=raw.get("state", "UNKNOWN").upper(),
            order_id=raw.get("uuid", ""),
        )

    def place_limit_order(
        self, symbol: str, side: str, quantity: float, price: float,
    ) -> OrderResult:
        market = _to_market(symbol)
        side_upper = side.upper()

        if side_upper == "SELL":
            balances = self.get_balances()
            asset = market.split("-", 1)[1]
            held = balances.get(asset, 0)
            if quantity > held:
                quantity = held
            if quantity <= 0:
                return OrderResult(
                    symbol=symbol, side=side_upper, quantity=0.0,
                    filled_quantity=0.0, avg_price=0.0,
                    status="REJECTED", error="no_holdings_to_sell",
                )

        notional = quantity * price
        if notional < MIN_ORDER_KRW:
            return OrderResult(
                symbol=symbol, side=side_upper, quantity=quantity,
                filled_quantity=0.0, avg_price=0.0,
                status="REJECTED", error=f"below_min_{notional:.0f}_KRW",
            )

        body = {
            "market": market,
            "side": "bid" if side_upper == "BUY" else "ask",
            "ord_type": "limit",
            "volume": f"{quantity:.8f}",
            "price": str(round(price, 0)),
        }
        try:
            raw = self._request("POST", "/v1/orders", body=body, signed=True)
        except Exception as e:
            return OrderResult(
                symbol=symbol, side=side_upper, quantity=quantity,
                filled_quantity=0.0, avg_price=0.0,
                status="ERROR", error=str(e)[:200],
            )
        return OrderResult(
            symbol=symbol,
            side=side_upper,
            quantity=quantity,
            filled_quantity=0.0,  # limit은 즉시 체결 아님
            avg_price=price,
            status=raw.get("state", "WAIT").upper(),
            order_id=raw.get("uuid", ""),
        )

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        try:
            self._request(
                "DELETE", "/v1/order",
                params={"uuid": order_id}, signed=True,
            )
            return True
        except Exception:
            return False

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        """미체결/체결 상태 조회 (부분체결 처리용)."""
        try:
            return self._request(
                "GET", "/v1/order",
                params={"uuid": order_id}, signed=True,
            )
        except Exception as e:
            return {"error": str(e)[:200]}

    def validate_credentials(self) -> bool:
        """API 키 유효성 + 권한 확인."""
        try:
            self._request("GET", "/v1/api_keys", signed=True)
            return True
        except Exception:
            return False
