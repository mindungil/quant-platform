import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import jwt

from app.core.auth import require_principal
from app.core.config import settings
from app.core.dashboard import build_dashboard_summary
from app.core.summary import gateway_summary
from app.models.auth import GatewayPrincipal
from app.services.gateway_client import GatewayClient

router = APIRouter()
auth_client = GatewayClient(settings.auth_service_base_url)
memory_client = GatewayClient(settings.memory_service_base_url)
strategy_client = GatewayClient(settings.strategy_registry_base_url)
signal_client = GatewayClient(settings.signal_service_base_url)
order_client = GatewayClient(settings.order_service_base_url)
credential_client = GatewayClient(settings.credential_store_base_url)
risk_client = GatewayClient(settings.risk_service_base_url)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/gateway/summary")
def summary() -> dict:
    return gateway_summary()


@router.get("/gateway/me", response_model=GatewayPrincipal)
def current_user(principal: GatewayPrincipal = Depends(require_principal)) -> GatewayPrincipal:
    return principal


@router.get("/me", response_model=GatewayPrincipal)
def current_user_public(principal: GatewayPrincipal = Depends(require_principal)) -> GatewayPrincipal:
    return principal


@router.post("/auth/register")
def gateway_register(payload: dict) -> JSONResponse:
    return JSONResponse(auth_client.post("/auth/register", json=payload))


@router.post("/auth/login")
def gateway_login(payload: dict) -> JSONResponse:
    return JSONResponse(auth_client.post("/auth/login", json=payload))


@router.post("/auth/refresh")
def gateway_refresh(payload: dict) -> JSONResponse:
    return JSONResponse(auth_client.post("/auth/refresh", json=payload))


@router.get("/gateway/dashboard")
def dashboard(principal: GatewayPrincipal = Depends(require_principal)) -> dict:
    return build_dashboard_summary(principal)


@router.get("/dashboard")
def dashboard_public(principal: GatewayPrincipal = Depends(require_principal)) -> dict:
    return build_dashboard_summary(principal)


@router.get("/gateway/signals")
def gateway_signals(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return JSONResponse(signal_client.get("/signals"))


@router.get("/signals")
def gateway_signals_public(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return JSONResponse(signal_client.get("/signals"))


@router.get("/gateway/feed")
def gateway_feed(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = memory_client.post(
        "/memory/search",
        headers=principal.forwarded_headers,
        json={
            "user_id": principal.user_id,
            "asset": "BTCUSDT",
            "asset_type": "crypto",
            "signal_score": 0.0,
            "action": "HOLD",
            "top_k": 20,
        },
    )
    return JSONResponse(result)


@router.get("/feed")
def gateway_feed_public(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return gateway_feed(principal)


@router.post("/gateway/memory/record")
def gateway_record_memory(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = memory_client.post("/memory/record", headers=principal.forwarded_headers, json=payload)
    return JSONResponse(result)


@router.post("/gateway/memory/search")
def gateway_search_memory(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = memory_client.post("/memory/search", headers=principal.forwarded_headers, json=payload)
    return JSONResponse(result)


@router.get("/gateway/strategies/active")
def gateway_active_strategy(asset_type: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = strategy_client.get(
        "/strategies/active",
        headers=principal.forwarded_headers,
        params={"asset_type": asset_type},
    )
    return JSONResponse(result)


@router.get("/strategies")
def gateway_active_strategy_public(
    asset_type: str = "crypto", principal: GatewayPrincipal = Depends(require_principal)
) -> JSONResponse:
    return gateway_active_strategy(asset_type=asset_type, principal=principal)


@router.post("/gateway/strategies")
def gateway_create_strategy(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = strategy_client.post("/strategies", headers=principal.forwarded_headers, json=payload)
    return JSONResponse(result)


@router.patch("/gateway/strategies/{strategy_id}/status")
def gateway_update_strategy_status(
    strategy_id: str, payload: dict, principal: GatewayPrincipal = Depends(require_principal)
) -> JSONResponse:
    result = strategy_client.patch(
        f"/strategies/{strategy_id}/status",
        headers=principal.forwarded_headers,
        json=payload,
    )
    return JSONResponse(result)


@router.get("/gateway/settings")
def gateway_settings(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    payload = {
        "credentials": [],
        "risk_defaults": {
            "max_notional": 10000,
            "exposure_limit": 50000,
            "warning_drawdown": 0.05,
            "liquidate_drawdown": 0.10,
        },
    }
    return JSONResponse(payload)


@router.get("/settings")
def gateway_settings_public(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return gateway_settings(principal)


@router.post("/gateway/settings/credentials")
def gateway_store_credentials(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = credential_client.post("/credentials", headers=principal.forwarded_headers, json=payload)
    return JSONResponse(result)


@router.post("/gateway/settings/risk")
def gateway_risk_check(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    merged = {"user_id": principal.user_id, **payload}
    result = risk_client.post("/risk/approve", json=merged)
    return JSONResponse(result)


@router.post("/gateway/orders")
def gateway_create_order(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    merged = {"user_id": principal.user_id, **payload}
    result = order_client.post("/orders", json=merged)
    return JSONResponse(result)


@router.post("/orders")
def gateway_create_order_public(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return gateway_create_order(payload=payload, principal=principal)


def _principal_from_websocket(websocket: WebSocket) -> GatewayPrincipal:
    token = websocket.query_params.get("token")
    if token is None:
        raise jwt.InvalidTokenError("missing_token")
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
    )
    return GatewayPrincipal(
        user_id=payload["sub"],
        email=payload.get("email"),
        roles=payload.get("roles", []),
        forwarded_headers={"X-User-ID": payload["sub"]},
    )


@router.websocket("/gateway/ws")
async def gateway_ws(websocket: WebSocket) -> None:
    try:
        principal = _principal_from_websocket(websocket)
    except jwt.InvalidTokenError:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        while True:
            snapshot = build_dashboard_summary(principal)
            orders = snapshot.get("orders", [])
            signals = snapshot.get("signals", []) if isinstance(snapshot.get("signals"), list) else []
            memory_probe = snapshot.get("memory_probe", {})
            feed_items = memory_probe.get("items", []) if isinstance(memory_probe, dict) else []

            if orders:
                latest_order = orders[-1]
                await websocket.send_json({"type": "order.filled", "data": latest_order})
            if signals:
                latest_signal = signals[0]
                await websocket.send_json(
                    {
                        "type": "signal.threshold",
                        "data": {
                            "asset": latest_signal.get("asset"),
                            "score": latest_signal.get("signal_score"),
                            "crossed": latest_signal.get("threshold_crossed"),
                        },
                    }
                )
                await websocket.send_json({"type": "feature.updated", "data": latest_signal})
            if feed_items:
                latest_feed = feed_items[0]["record"]
                await websocket.send_json(
                    {
                        "type": "agent.decision",
                        "data": {
                            "asset": latest_feed.get("asset"),
                            "action": latest_feed.get("action"),
                            "reasoning": latest_feed.get("reasoning"),
                        },
                    }
                )

            statistics = snapshot.get("statistics", {})
            if isinstance(statistics, dict):
                await websocket.send_json(
                    {
                        "type": "risk.triggered",
                        "data": {
                            "level": "WARNING" if statistics.get("drift_detected") else "NORMAL",
                            "drawdown": statistics.get("max_drawdown", 0.0),
                        },
                    }
                )
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return


@router.websocket("/ws")
async def public_gateway_ws(websocket: WebSocket) -> None:
    await gateway_ws(websocket)
