from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

import aiohttp

from common import REPO_ROOT, bearer_headers, ensure_registered, load_env, login, request_json, service_url, wait_for_http

_DEMO_PASSWORD = "Password123A"


def _run_demo_flow() -> None:
    subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "demo_flow.py")], check=True)


def _stage(name: str) -> None:
    print(f"[smoke-e2e] {name}")


async def _receive_product_event(ws_url: str, gateway_base: str, headers: dict[str, str]) -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.ws_connect(ws_url) as socket:
            async with session.post(
                f"{gateway_base}/orders",
                headers=headers,
                json={
                    "exchange": "binance",
                    "asset": "BTCUSDT",
                    "side": "BUY",
                    "quantity": 0.02,
                    "price": 84600,
                    "requested_notional": 1692,
                    "max_notional": 5000,
                    "current_drawdown": 0.01,
                    "current_exposure": 1000,
                    "exposure_limit": 50000,
                    "automation_enabled": True,
                    "shadow_mode": True,
                },
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"manual order failed: {response.status} {await response.text()}")
            deadline = time.time() + 12
            while time.time() < deadline:
                message = await socket.receive(timeout=12)
                if message.type == aiohttp.WSMsgType.TEXT:
                    payload = json.loads(message.data)
                    if payload.get("type") in {"order.partially_filled", "order.filled", "portfolio.updated", "statistics.updated"}:
                        return payload
                elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    break
    raise RuntimeError("websocket did not deliver a product event")


def main() -> None:
    load_env()
    gateway_base = service_url("HOST_API_GATEWAY_BASE_URL", "http://localhost:8017")
    frontend_base = service_url("HOST_FRONTEND_BASE_URL", "http://localhost:8018")

    _stage("wait: gateway")
    wait_for_http(f"{gateway_base}/health")
    _stage("wait: frontend")
    wait_for_http(frontend_base)
    _stage("run: demo-flow")
    _run_demo_flow()

    _stage("auth login")
    ensure_registered(
        gateway_base,
        email="demo@example.com",
        password=_DEMO_PASSWORD,
        display_name="Demo Operator",
        plan="premium",
    )
    login_response = login(gateway_base, email="demo@example.com", password=_DEMO_PASSWORD)
    token = login_response["access_token"]
    headers = bearer_headers(token)

    _stage("load dashboard")
    dashboard = request_json("GET", f"{gateway_base}/dashboard", headers=headers)
    _stage("load signals")
    signals = request_json("GET", f"{gateway_base}/signals", headers=headers)
    _stage("load feed")
    feed = request_json("GET", f"{gateway_base}/feed", headers=headers)
    _stage("inspect durable execution state")
    orders = request_json("GET", f"{gateway_base}/orders", headers=headers)
    portfolio = request_json("GET", f"{gateway_base}/portfolio", headers=headers)
    statistics = request_json("GET", f"{gateway_base}/performance", headers=headers)
    if not orders:
        raise RuntimeError("demo-flow did not persist orders")
    if not portfolio.get("positions"):
        raise RuntimeError("demo-flow did not update portfolio positions")
    if statistics.get("trade_count", 0) < 1:
        raise RuntimeError("demo-flow did not update statistics")

    ws_base = gateway_base.replace("http://", "ws://").replace("https://", "wss://")
    _stage("open websocket and await product event")
    event_payload = asyncio.run(_receive_product_event(f"{ws_base}/gateway/ws?token={token}", gateway_base, headers))
    if event_payload.get("type") not in {"order.partially_filled", "order.filled", "portfolio.updated", "statistics.updated"}:
        raise RuntimeError(f"unexpected websocket event: {event_payload}")

    print(
        json.dumps(
            {
                "auth_login": login_response["claims"]["sub"],
                "dashboard_loaded": bool(dashboard),
                "signals_loaded": len(signals),
                "feed_loaded": len(feed.get("items", [])),
                "orders_loaded": len(orders),
                "portfolio_positions": len(portfolio.get("positions", {})),
                "trade_count": statistics.get("trade_count", 0),
                "websocket_event": event_payload.get("type") if event_payload else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
