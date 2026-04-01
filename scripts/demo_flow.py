from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin
from typing import Any

from common import bearer_headers, ensure_registered, load_env, login, print_json, request_json, service_url, wait_for_http


def _stage(name: str) -> None:
    print(f"[demo-flow] {name}")


def _wait_for_json(url: str, *, timeout_seconds: int = 30) -> dict[str, Any] | list[Any]:
    import time

    deadline = time.time() + timeout_seconds
    last_error = "timeout"
    while time.time() < deadline:
        try:
            return request_json("GET", url)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1.0)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def _seed_strategy(gateway_base: str, token: str) -> dict[str, Any]:
    strategy = request_json(
        "POST",
        f"{gateway_base}/gateway/strategies",
        headers=bearer_headers(token),
        payload={
            "name": "Operator Demo Momentum",
            "asset_type": "crypto",
            "indicators": ["rsi_14", "macd", "sma_20", "vwap"],
            "weights": {"rsi": 0.25, "macd": 0.25, "sma_20": 0.25, "vwap": 0.25},
            "thresholds": {"entry": 0.5, "exit": -0.5},
            "version": "demo-v1",
        },
    )
    return request_json(
        "PATCH",
        f"{gateway_base}/gateway/strategies/{strategy['id']}/status",
        headers=bearer_headers(token),
        payload={"status": "ACTIVE"},
    )


def _promote_signal_crossing_strategy(gateway_base: str, token: str) -> dict[str, Any]:
    strategy = request_json(
        "POST",
        f"{gateway_base}/gateway/strategies",
        headers=bearer_headers(token),
        payload={
            "name": "Operator Demo Momentum Tight",
            "asset_type": "crypto",
            "indicators": ["rsi_14", "macd", "sma_20", "vwap"],
            "weights": {"rsi": 0.25, "macd": 0.25, "sma_20": 0.25, "vwap": 0.25},
            "thresholds": {"entry": 0.3, "exit": -0.3},
            "version": "demo-v2-tight",
        },
    )
    return request_json(
        "PATCH",
        f"{gateway_base}/gateway/strategies/{strategy['id']}/status",
        headers=bearer_headers(token),
        payload={"status": "ACTIVE"},
    )


def _seed_candles(market_base: str) -> None:
    # Repeated runs must remain monotonic even when the service keeps the latest
    # candle in memory across requests.
    start = datetime.now(UTC) + timedelta(minutes=1)
    try:
        latest = request_json("GET", f"{market_base}/candles/BTCUSDT/latest")
        latest_timestamp = datetime.fromisoformat(latest["timestamp"])
        start = max(start, latest_timestamp + timedelta(minutes=1))
    except Exception:
        pass
    for index in range(30):
        price = 81000 + (index * 120) + sin(index / 3) * 40
        candle = {
            "timestamp": (start + timedelta(minutes=index)).isoformat(),
            "open": round(price - 30, 2),
            "high": round(price + 55, 2),
            "low": round(price - 60, 2),
            "close": round(price + 40, 2),
            "volume": round(900 + index * 12, 2),
        }
        request_json("POST", f"{market_base}/candles/BTCUSDT", payload=candle, expected_status=(200, 201))


def main() -> None:
    load_env()
    gateway_base = service_url("HOST_API_GATEWAY_BASE_URL", "http://localhost:8017")
    market_base = service_url("HOST_MARKET_DATA_BASE_URL", "http://localhost:8001")
    signal_base = service_url("HOST_SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    crypto_base = service_url("HOST_CRYPTO_AGENT_BASE_URL", "http://localhost:8006")
    order_base = service_url("HOST_ORDER_SERVICE_BASE_URL", "http://localhost:8011")
    portfolio_base = service_url("HOST_PORTFOLIO_SERVICE_BASE_URL", "http://localhost:8012")
    statistics_base = service_url("HOST_STATISTICS_SERVICE_BASE_URL", "http://localhost:8013")

    _stage("waiting for services")
    for base in (gateway_base, market_base, signal_base, crypto_base, order_base, portfolio_base, statistics_base):
        wait_for_http(f"{base}/health")

    _stage("register/login demo operator")
    ensure_registered(
        gateway_base,
        email="demo@example.com",
        password="password123",
        display_name="Demo Operator",
        plan="premium",
    )
    login_response = login(gateway_base, email="demo@example.com", password="password123")
    token = login_response["access_token"]

    _stage("seed active strategy and credentials")
    strategy = _seed_strategy(gateway_base, token)
    request_json(
        "POST",
        f"{gateway_base}/gateway/settings/credentials",
        headers=bearer_headers(token),
        payload={
            "exchange": "binance",
            "api_key": "demo-api-key-1234",
            "api_secret": "demo-api-secret-1234",
            "label": "compose-demo",
            "sandbox": True,
        },
    )
    _stage("ingest market candles")
    _seed_candles(market_base)
    _stage("evaluate signal and run decision")
    user_headers = {"X-User-ID": login_response["claims"]["sub"]}
    signal = request_json("POST", f"{signal_base}/signals/evaluate/BTCUSDT", headers=user_headers)
    if not signal.get("threshold_crossed", False):
        _stage("tighten demo strategy thresholds and re-evaluate")
        strategy = _promote_signal_crossing_strategy(gateway_base, token)
        signal = request_json("POST", f"{signal_base}/signals/evaluate/BTCUSDT", headers=user_headers)
    if not signal.get("threshold_crossed", False):
        raise RuntimeError(f"expected demo signal to cross threshold, got {signal}")
    decision = request_json("POST", f"{crypto_base}/decisions/run/BTCUSDT", headers=user_headers)
    _stage("wait for durable execution artifacts")
    orders = _wait_for_json(f"{order_base}/orders/{login_response['claims']['sub']}")
    if not orders:
        raise RuntimeError("expected order-service to persist at least one order")
    order = orders[-1]
    portfolio = _wait_for_json(f"{portfolio_base}/portfolio/{login_response['claims']['sub']}")
    statistics = _wait_for_json(f"{statistics_base}/statistics/{login_response['claims']['sub']}")
    dashboard = request_json("GET", f"{gateway_base}/dashboard", headers=bearer_headers(token))
    feed = request_json("GET", f"{gateway_base}/feed", headers=bearer_headers(token))
    signals = request_json("GET", f"{gateway_base}/signals", headers=bearer_headers(token))
    if not feed.get("items"):
        raise RuntimeError("expected feed to include decision memory items")
    if not portfolio.get("positions"):
        raise RuntimeError("expected portfolio positions to be updated")
    if statistics.get("trade_count", 0) < 1:
        raise RuntimeError("expected statistics to include at least one trade")

    print_json(
        {
            "login": {"email": "demo@example.com"},
            "strategy": strategy,
            "signal": signal,
            "decision": decision,
            "order": order,
            "portfolio_positions": portfolio.get("positions", {}),
            "statistics": statistics,
            "dashboard_keys": sorted(dashboard.keys()),
            "feed_items": len(feed.get("items", [])),
            "signal_count": len(signals),
        }
    )


if __name__ == "__main__":
    main()
