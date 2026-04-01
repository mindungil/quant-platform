from __future__ import annotations

import json
import subprocess
import sys
import time

from websocket import create_connection

from common import REPO_ROOT, bearer_headers, ensure_registered, load_env, login, request_json, service_url, wait_for_http


def _run_demo_flow() -> None:
    subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "demo_flow.py")], check=True)


def main() -> None:
    load_env()
    gateway_base = service_url("HOST_API_GATEWAY_BASE_URL", "http://localhost:8017")
    frontend_base = service_url("HOST_FRONTEND_BASE_URL", "http://localhost:8018")

    wait_for_http(f"{gateway_base}/health")
    wait_for_http(frontend_base)
    _run_demo_flow()

    ensure_registered(
        gateway_base,
        email="demo@example.com",
        password="password123",
        display_name="Demo Operator",
        plan="premium",
    )
    login_response = login(gateway_base, email="demo@example.com", password="password123")
    token = login_response["access_token"]
    headers = bearer_headers(token)

    dashboard = request_json("GET", f"{gateway_base}/dashboard", headers=headers)
    signals = request_json("GET", f"{gateway_base}/signals", headers=headers)
    feed = request_json("GET", f"{gateway_base}/feed", headers=headers)

    ws_base = gateway_base.replace("http://", "ws://").replace("https://", "wss://")
    socket = create_connection(f"{ws_base}/ws?token={token}", timeout=10)
    try:
        request_json(
            "POST",
            f"{gateway_base}/orders",
            headers=headers,
            payload={
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
        )
        deadline = time.time() + 10
        event_payload = None
        while time.time() < deadline:
            event_payload = json.loads(socket.recv())
            if event_payload.get("type") in {"order.filled", "portfolio.updated", "statistics.updated"}:
                break
        if event_payload is None:
            raise RuntimeError("websocket did not deliver a product event")
    finally:
        socket.close()

    print(
        json.dumps(
            {
                "auth_login": login_response["claims"]["sub"],
                "dashboard_loaded": bool(dashboard),
                "signals_loaded": len(signals),
                "feed_loaded": len(feed.get("items", [])),
                "websocket_event": event_payload.get("type") if event_payload else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
