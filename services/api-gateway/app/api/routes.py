import json

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import httpx
import jwt
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.auth import build_internal_admin_headers, require_principal, require_role
from app.core.config import settings
from app.core.dashboard import build_dashboard_summary
from app.core.summary import gateway_summary
from app.models.auth import GatewayPrincipal
from app.services.gateway_client import GatewayClient
from shared.health import check_redis, check_tcp, health_payload
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus

router = APIRouter()
auth_client = GatewayClient(settings.auth_service_base_url)
memory_client = GatewayClient(settings.memory_service_base_url)
strategy_client = GatewayClient(settings.strategy_registry_base_url)
signal_client = GatewayClient(settings.signal_service_base_url)
order_client = GatewayClient(settings.order_service_base_url)
credential_client = GatewayClient(settings.credential_store_base_url)
risk_client = GatewayClient(settings.risk_service_base_url)
backtest_client = GatewayClient(settings.backtest_service_base_url)
agent_client = GatewayClient(settings.crypto_agent_base_url)
portfolio_client = GatewayClient(settings.portfolio_service_base_url)
statistics_client = GatewayClient(settings.statistics_service_base_url)
realtime_bus = RealtimeBus(RedisStore(settings.redis_url), replay_limit=settings.realtime_replay_limit)


def _proxy_json(response: httpx.Response) -> JSONResponse:
    try:
        payload = response.json() if response.text else {}
    except ValueError:
        payload = {"detail": response.text}
    return JSONResponse(payload, status_code=response.status_code)


def _probe_service(name: str, base_url: str) -> dict[str, str]:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=3.0)
        response.raise_for_status()
        payload = response.json()
        return {"status": payload.get("status", "ok"), "base_url": base_url}
    except Exception as exc:
        return {"status": "error", "base_url": base_url, "detail": str(exc)}


@router.get("/health")
def health() -> dict:
    return health_payload(
        "api-gateway",
        {
            "redis": check_redis("redis", settings.redis_url),
            "auth-service": check_tcp("auth-service", settings.auth_service_base_url, default_port=8000),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
    return _proxy_json(auth_client.request("POST", "/auth/register", json=payload))


@router.post("/auth/login")
def gateway_login(payload: dict) -> JSONResponse:
    return _proxy_json(auth_client.request("POST", "/auth/login", json=payload))


@router.post("/auth/refresh")
def gateway_refresh(payload: dict) -> JSONResponse:
    return _proxy_json(auth_client.request("POST", "/auth/refresh", json=payload))


@router.get("/gateway/dashboard")
def dashboard(principal: GatewayPrincipal = Depends(require_principal)) -> dict:
    return build_dashboard_summary(principal)


@router.get("/dashboard")
def dashboard_public(principal: GatewayPrincipal = Depends(require_principal)) -> dict:
    return build_dashboard_summary(principal)


@router.get("/gateway/signals")
def gateway_signals(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return JSONResponse(signal_client.get("/signals", headers=principal.forwarded_headers))


@router.get("/signals")
def gateway_signals_public(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return JSONResponse(signal_client.get("/signals", headers=principal.forwarded_headers))


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
def list_strategies(request: Request, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    params = dict(request.query_params)
    result = strategy_client.get("/strategies", headers=principal.forwarded_headers, params=params)
    return JSONResponse(result)


@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = strategy_client.get(f"/strategies/{strategy_id}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.post("/strategies")
def create_strategy(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = strategy_client.post("/strategies", headers=principal.forwarded_headers, json=payload)
    return JSONResponse(result)


@router.patch("/strategies/{strategy_id}/backtest")
def update_strategy_backtest(
    strategy_id: str, payload: dict, principal: GatewayPrincipal = Depends(require_principal)
) -> JSONResponse:
    response = strategy_client.request(
        "PATCH", f"/strategies/{strategy_id}/backtest", headers=principal.forwarded_headers, json=payload
    )
    return _proxy_json(response)


@router.patch("/strategies/{strategy_id}/status")
def update_strategy_status(
    strategy_id: str, payload: dict, principal: GatewayPrincipal = Depends(require_principal)
) -> JSONResponse:
    response = strategy_client.request(
        "PATCH", f"/strategies/{strategy_id}/status", headers=principal.forwarded_headers, json=payload
    )
    return _proxy_json(response)


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = strategy_client.request("DELETE", f"/strategies/{strategy_id}", headers=principal.forwarded_headers)
    return _proxy_json(response)


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
    credentials: list[dict] = []
    for exchange in ("binance", "upbit", "alpaca"):
        try:
            credentials.append(
                credential_client.get(
                    f"/credentials/{principal.user_id}/{exchange}",
                    headers=principal.forwarded_headers,
                )
            )
        except Exception:
            continue
    payload = {
        "credentials": credentials,
        "risk_defaults": {
            "max_notional": 10000,
            "exposure_limit": 50000,
            "warning_drawdown": 0.05,
            "liquidate_drawdown": 0.10,
        },
        "execution": {
            "live_trading_enabled": settings.live_trading_enabled,
            "default_shadow_mode": settings.default_shadow_mode,
            "allowed_exchanges": list(settings.allowed_live_exchanges),
            "strict_runtime": settings.strict_runtime,
        },
    }
    if "admin" in principal.roles:
        payload["execution"] = order_client.get(
            "/admin/execution/config",
            headers=build_internal_admin_headers(principal, "/admin/execution/config"),
        )
    return JSONResponse(payload)


@router.get("/settings")
def gateway_settings_public(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return gateway_settings(principal)


@router.post("/gateway/settings/credentials")
def gateway_store_credentials(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    merged = {"user_id": principal.user_id, **payload}
    response = credential_client.request("POST", "/credentials", headers=principal.forwarded_headers, json=merged)
    return _proxy_json(response)


@router.post("/gateway/settings/risk")
def gateway_risk_check(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    merged = {"user_id": principal.user_id, **payload}
    response = risk_client.request("POST", "/risk/approve", json=merged)
    return _proxy_json(response)


@router.post("/gateway/orders")
def gateway_create_order(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    merged = {"user_id": principal.user_id, **payload}
    response = order_client.request("POST", "/orders", json=merged)
    return _proxy_json(response)


@router.post("/orders")
def gateway_create_order_public(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    return gateway_create_order(payload=payload, principal=principal)


# ── Order Management (proxy to order-service) ──────────────────────────


@router.get("/orders")
def list_orders(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = order_client.get(f"/orders/{principal.user_id}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/orders/{order_id}")
def get_order(order_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = order_client.get(f"/orders/detail/{order_id}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.delete("/orders/{order_id}")
def cancel_order(order_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = order_client.request("DELETE", f"/orders/{order_id}", headers=principal.forwarded_headers)
    return _proxy_json(response)


@router.get("/orders/{order_id}/protections")
def get_order_protections(order_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = order_client.get(f"/orders/protections/{order_id}", headers=principal.forwarded_headers)
    return JSONResponse(result)


# ── Backtest (proxy to backtest-service) ────────────────────────────────


@router.post("/backtests")
def run_backtest(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = backtest_client.request("POST", "/backtests/run", headers=principal.forwarded_headers, json=payload)
    return _proxy_json(response)


@router.get("/backtests/{job_id}")
def get_backtest(job_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = backtest_client.request("GET", f"/backtests/{job_id}", headers=principal.forwarded_headers)
    return _proxy_json(response)


# ── Credential Management (proxy to credential-store) ──────────────────


@router.post("/settings/credentials")
def store_credentials(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    merged = {"user_id": principal.user_id, **payload}
    response = credential_client.request("POST", "/credentials", headers=principal.forwarded_headers, json=merged)
    return _proxy_json(response)


@router.get("/settings/credentials")
def list_credentials(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    try:
        result = credential_client.get(f"/credentials/{principal.user_id}", headers=principal.forwarded_headers)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return JSONResponse([])
        raise
    if isinstance(result, dict):
        return JSONResponse(result.get("credentials", []))
    return JSONResponse(result if isinstance(result, list) else [])


@router.delete("/settings/credentials/{exchange}")
def delete_credentials(exchange: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = credential_client.request(
        "DELETE", f"/credentials/{principal.user_id}/{exchange}", headers=principal.forwarded_headers
    )
    return _proxy_json(response)


# ── Risk Settings (proxy to risk-service) ─────────────────────────────


@router.get("/settings/risk")
def get_risk_settings(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = risk_client.get(f"/risk/settings/{principal.user_id}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.put("/settings/risk")
def update_risk_settings(payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = risk_client.request("PUT", f"/risk/settings/{principal.user_id}", json=payload)
    return _proxy_json(response)


# ── Portfolio (proxy to portfolio-service) ──────────────────────────────


@router.get("/portfolio")
def get_portfolio(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = portfolio_client.get(f"/portfolio/{principal.user_id}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.post("/portfolio/optimize")
def optimize_portfolio(payload: dict = {}, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = portfolio_client.request("POST", f"/portfolio/{principal.user_id}/optimize", json=payload)
    return _proxy_json(response)


# ── Statistics (proxy to statistics-service) ────────────────────────────


@router.get("/statistics")
def get_statistics(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = statistics_client.request("GET", f"/statistics/{principal.user_id}", headers=principal.forwarded_headers)
    return _proxy_json(response)


# ── Agent Decisions (proxy to crypto-agent) ─────────────────────────────


@router.post("/decisions/run/{asset}")
def run_decision(asset: str, payload: dict, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = agent_client.request(
        "POST", f"/decisions/run/{asset}", headers=principal.forwarded_headers, json=payload
    )
    return _proxy_json(response)


@router.get("/decisions/latest/{asset}")
def get_latest_decision(asset: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = agent_client.get(f"/decisions/latest/{asset}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/decisions/history/{asset}")
def get_decision_history(asset: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = agent_client.get(f"/decisions/history/{asset}", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/recommendations/{asset}")
def get_recommendations(asset: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = agent_client.get(f"/recommendations/{asset}", headers=principal.forwarded_headers)
    return JSONResponse(result)


# ── Admin: Live Trading Gate (proxy to order-service) ──────────────────


@router.post("/admin/execution/pre-flight")
def admin_pre_flight(
    payload: dict, principal: GatewayPrincipal = Depends(require_role("admin"))
) -> JSONResponse:
    response = order_client.request(
        "POST",
        "/admin/execution/pre-flight",
        headers=build_internal_admin_headers(principal, "/admin/execution/pre-flight"),
        json=payload,
    )
    return _proxy_json(response)


@router.post("/admin/execution/enable-live")
def admin_enable_live(
    payload: dict, principal: GatewayPrincipal = Depends(require_role("admin"))
) -> JSONResponse:
    response = order_client.request(
        "POST",
        "/admin/execution/enable-live",
        headers=build_internal_admin_headers(principal, "/admin/execution/enable-live"),
        json=payload,
    )
    return _proxy_json(response)


@router.post("/admin/execution/emergency-stop")
def admin_emergency_stop(
    payload: dict, principal: GatewayPrincipal = Depends(require_role("admin"))
) -> JSONResponse:
    response = order_client.request(
        "POST",
        "/admin/execution/emergency-stop",
        headers=build_internal_admin_headers(principal, "/admin/execution/emergency-stop"),
        json=payload,
    )
    return _proxy_json(response)


@router.get("/admin/users")
def admin_users(principal: GatewayPrincipal = Depends(require_role("admin"))) -> JSONResponse:
    result = auth_client.get(
        "/admin/users",
        headers=build_internal_admin_headers(principal, "/admin/users"),
    )
    return JSONResponse(result)


@router.patch("/admin/users/{user_id}/roles")
def admin_update_user_roles(
    user_id: str,
    payload: dict,
    principal: GatewayPrincipal = Depends(require_role("admin")),
) -> JSONResponse:
    result = auth_client.patch(
        f"/admin/users/{user_id}/roles",
        headers=build_internal_admin_headers(principal, f"/admin/users/{user_id}/roles"),
        json=payload,
    )
    return JSONResponse(result)


@router.get("/admin/system/health")
def admin_system_health(principal: GatewayPrincipal = Depends(require_role("admin"))) -> dict:
    services = {
        "api-gateway": {"status": "ok", "base_url": "self"},
        "auth-service": _probe_service("auth-service", settings.auth_service_base_url),
        "market-data": _probe_service("market-data", settings.market_data_base_url),
        "feature-store": _probe_service("feature-store", settings.feature_store_base_url),
        "signal-service": _probe_service("signal-service", settings.signal_service_base_url),
        "memory-service": _probe_service("memory-service", settings.memory_service_base_url),
        "strategy-registry": _probe_service("strategy-registry", settings.strategy_registry_base_url),
        "crypto-agent": _probe_service("crypto-agent", settings.crypto_agent_base_url),
        "risk-service": _probe_service("risk-service", settings.risk_service_base_url),
        "credential-store": _probe_service("credential-store", settings.credential_store_base_url),
        "order-service": _probe_service("order-service", settings.order_service_base_url),
        "portfolio-service": _probe_service("portfolio-service", settings.portfolio_service_base_url),
        "statistics-service": _probe_service("statistics-service", settings.statistics_service_base_url),
        "external-data-service": _probe_service("external-data-service", settings.external_data_service_base_url),
        "llm-gateway": _probe_service("llm-gateway", settings.llm_gateway_base_url),
    }
    overall = "ok" if all(item["status"] == "ok" for item in services.values()) else "degraded"
    return {
        "status": overall,
        "services": services,
        "redis_replay_bus": {"status": "ok" if RedisStore(settings.redis_url).ping() else "error"},
        "runtime_flags": {
            "strict_runtime": settings.strict_runtime,
            "live_trading_enabled": settings.live_trading_enabled,
            "default_shadow_mode": settings.default_shadow_mode,
            "allowed_live_exchanges": list(settings.allowed_live_exchanges),
        },
    }


@router.get("/admin/system/events")
def admin_system_events(limit: int = 50, principal: GatewayPrincipal = Depends(require_role("admin"))) -> dict:
    items = realtime_bus.recent(limit=min(max(limit, 1), settings.realtime_replay_limit))
    return {"items": items, "requested_by": principal.user_id}


@router.get("/admin/execution/config")
def admin_execution_config(principal: GatewayPrincipal = Depends(require_role("admin"))) -> JSONResponse:
    result = order_client.get(
        "/admin/execution/config",
        headers=build_internal_admin_headers(principal, "/admin/execution/config"),
    )
    return JSONResponse(result)


@router.patch("/admin/execution/config")
def admin_update_execution_config(
    payload: dict,
    principal: GatewayPrincipal = Depends(require_role("admin")),
) -> JSONResponse:
    result = order_client.patch(
        "/admin/execution/config",
        headers=build_internal_admin_headers(principal, "/admin/execution/config"),
        json=payload,
    )
    return JSONResponse(result)


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
    pubsub = None
    seen_event_ids: set[str] = set()
    try:
        for event in realtime_bus.recent(user_id=principal.user_id, limit=settings.realtime_replay_limit):
            seen_event_ids.add(event["event_id"])
            await websocket.send_json({"type": event["type"], "data": event["data"]})

        pubsub = await realtime_bus.subscribe(user_id=principal.user_id)
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if message is None:
                continue
            event = json.loads(message["data"]) if isinstance(message["data"], str) else message["data"]
            event_id = event.get("event_id")
            if event_id in seen_event_ids:
                continue
            if event_id is not None:
                seen_event_ids.add(event_id)
                if len(seen_event_ids) > settings.realtime_replay_limit * 4:
                    seen_event_ids = set(list(seen_event_ids)[-settings.realtime_replay_limit * 2 :])
            await websocket.send_json({"type": event["type"], "data": event["data"]})
    except WebSocketDisconnect:
        return
    finally:
        if pubsub is not None:
            await pubsub.aclose()


@router.websocket("/ws")
async def public_gateway_ws(websocket: WebSocket) -> None:
    await gateway_ws(websocket)
