from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sin
from typing import Any

from common import bearer_headers, ensure_registered, load_env, login, print_json, request_json, service_url, wait_for_http


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
            "thresholds": {"entry": 0.6, "exit": -0.6},
            "version": "demo-v1",
        },
    )
    return request_json(
        "PATCH",
        f"{gateway_base}/gateway/strategies/{strategy['id']}/status",
        headers=bearer_headers(token),
        payload={"status": "ACTIVE"},
    )


def _seed_candles(market_base: str, feature_base: str) -> None:
    start = datetime.now(UTC) - timedelta(minutes=35)
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
        request_json("POST", f"{feature_base}/events/candles/BTCUSDT", payload=candle, expected_status=(200, 201))


def main() -> None:
    load_env()
    gateway_base = service_url("HOST_API_GATEWAY_BASE_URL", "http://localhost:8017")
    market_base = service_url("HOST_MARKET_DATA_BASE_URL", "http://localhost:8001")
    feature_base = service_url("HOST_FEATURE_STORE_BASE_URL", "http://localhost:8002")
    signal_base = service_url("HOST_SIGNAL_SERVICE_BASE_URL", "http://localhost:8003")
    crypto_base = service_url("HOST_CRYPTO_AGENT_BASE_URL", "http://localhost:8006")

    for base in (gateway_base, market_base, feature_base, signal_base, crypto_base):
        wait_for_http(f"{base}/health")

    ensure_registered(
        gateway_base,
        email="demo@example.com",
        password="password123",
        display_name="Demo Operator",
        plan="premium",
    )
    login_response = login(gateway_base, email="demo@example.com", password="password123")
    token = login_response["access_token"]

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
    _seed_candles(market_base, feature_base)
    signal = request_json("POST", f"{signal_base}/signals/evaluate/BTCUSDT")
    decision = request_json("POST", f"{crypto_base}/decisions/run/BTCUSDT")

    request_json(
        "POST",
        f"{gateway_base}/gateway/memory/record",
        headers=bearer_headers(token),
        payload={
            "asset": decision["asset"],
            "asset_type": decision["asset_type"],
            "signal_score": decision["signal_score"],
            "action": decision["action"],
            "strategy_id": decision["strategy_id"],
            "reasoning": decision["reasoning"],
            "metadata": {
                "source": "demo-flow",
                "strategy_name": decision["strategy_name"],
                "threshold_crossed": decision["threshold_crossed"],
                "components": decision["components"],
            },
        },
    )

    order = request_json(
        "POST",
        f"{gateway_base}/orders",
        headers=bearer_headers(token),
        payload={
            "exchange": "binance",
            "asset": "BTCUSDT",
            "side": decision["action"],
            "quantity": 0.01,
            "price": 84500,
            "requested_notional": 845,
            "max_notional": 5000,
            "current_drawdown": 0.01,
            "current_exposure": 1000,
            "exposure_limit": 50000,
            "automation_enabled": True,
            "shadow_mode": True,
        },
    )

    dashboard = request_json("GET", f"{gateway_base}/dashboard", headers=bearer_headers(token))
    feed = request_json("GET", f"{gateway_base}/feed", headers=bearer_headers(token))
    signals = request_json("GET", f"{gateway_base}/signals", headers=bearer_headers(token))

    print_json(
        {
            "login": {"email": "demo@example.com"},
            "strategy": strategy,
            "signal": signal,
            "decision": decision,
            "order": order,
            "dashboard_keys": sorted(dashboard.keys()),
            "feed_items": len(feed.get("items", [])),
            "signal_count": len(signals),
        }
    )


if __name__ == "__main__":
    main()
