import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import httpx
import jwt
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.auth import build_internal_admin_headers, check_feature, get_tier_features, require_principal, require_role
from app.core.config import settings
from app.core.dashboard import build_dashboard_summary
from app.core.summary import gateway_summary
from app.models.auth import GatewayPrincipal
from app.services.gateway_client import GatewayClient
from shared.health import check_redis, check_tcp, health_payload
from shared.persistence import RedisStore
from shared.realtime import RealtimeBus

router = APIRouter()
market_data_client = GatewayClient(settings.market_data_base_url)
auth_client = GatewayClient(settings.auth_service_base_url)
memory_client = GatewayClient(settings.memory_service_base_url)
strategy_client = GatewayClient(settings.strategy_registry_base_url)
signal_client = GatewayClient(settings.signal_service_base_url)
order_client = GatewayClient(settings.order_service_base_url)
credential_client = GatewayClient(settings.credential_store_base_url)
risk_client = GatewayClient(settings.risk_service_base_url)
backtest_client = GatewayClient(settings.backtest_service_base_url)
agent_client = GatewayClient(settings.crypto_agent_base_url)
llm_client = GatewayClient(settings.llm_gateway_base_url, timeout=120.0)
orchestrator_client = GatewayClient(settings.orchestrator_agent_base_url)
portfolio_client = GatewayClient(settings.portfolio_service_base_url)
statistics_client = GatewayClient(settings.statistics_service_base_url)
_redis_store = RedisStore(settings.redis_url)
realtime_bus = RealtimeBus(_redis_store, replay_limit=settings.realtime_replay_limit)


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


@router.post("/auth/logout")
def gateway_logout(request: Request) -> JSONResponse:
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("authorization",)}
    response = auth_client.request("POST", "/auth/logout", headers=headers)
    return _proxy_json(response)


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
    if not check_feature(principal, "can_trade"):
        raise HTTPException(
            status_code=403,
            detail="upgrade_required: 주문 실행은 Pro 이상 플랜에서 이용 가능합니다",
        )
    merged = {"user_id": principal.user_id, **payload}
    response = order_client.request(
        "POST", "/orders",
        headers=build_internal_admin_headers(principal, "/orders"),
        json=merged,
    )
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


@router.get("/statistics/hindsight/{asset}")
def get_hindsight(asset: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    response = statistics_client.request(
        "GET", f"/statistics/hindsight/{asset}", headers=principal.forwarded_headers
    )
    return _proxy_json(response)


@router.get("/track-record/{asset}")
async def get_track_record_public(asset: str) -> JSONResponse:
    """Public agent performance — no authentication required."""
    stats_url = settings.statistics_service_base_url
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{stats_url.rstrip('/')}/statistics/paper-portfolio/{asset}",
                timeout=10.0,
            )
            return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


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
    # Apply tier-based decisions limit
    plan = getattr(principal, "plan", "FREE") or "FREE"
    features = get_tier_features(plan)
    limit = features["decisions_limit"]
    if isinstance(result, list) and len(result) > limit:
        result = result[-limit:]  # keep the most recent entries
    return JSONResponse(result)


@router.get("/agent/status")
def get_agent_status(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = agent_client.get("/agent/status", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/recommendations/{asset}")
def get_recommendations(asset: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = agent_client.get(f"/recommendations/{asset}", headers=principal.forwarded_headers)
    return JSONResponse(result)


# ── Agent Chat (proxy to llm-gateway) ────��───────────────────────────


@router.post("/chat")
async def chat(request: Request, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    """에이전트 채팅 — LLM Gateway의 에이전틱 루프 호출."""
    # Check daily chat limit based on tier
    plan = getattr(principal, "plan", "FREE") or "FREE"
    features = get_tier_features(plan)
    daily_key = f"chat_daily:{principal.user_id}:{date.today()}"
    try:
        r = _redis_store._client
        if r is not None:
            count = r.incr(daily_key)
            if count == 1:
                r.expire(daily_key, 86400)
            if count > features["chat_daily_limit"]:
                raise HTTPException(
                    status_code=429,
                    detail="chat_limit_exceeded: 일일 채팅 한도를 초과했습니다. 플랜을 업그레이드하세요.",
                )
    except HTTPException:
        raise
    except Exception:
        pass  # fail open if Redis is unavailable

    body = await request.json()
    response = llm_client.request(
        "POST", "/chat",
        headers={**principal.forwarded_headers, "Content-Type": "application/json"},
        json=body,
    )
    return _proxy_json(response)


@router.get("/conversations")
def get_conversations(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = llm_client.get("/conversations", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/conversations/{conversation_id}/messages")
def get_conv_messages(conversation_id: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = llm_client.get(f"/conversations/{conversation_id}/messages", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/chat/tools")
def get_agent_tools(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = llm_client.get("/tools", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/auth/{provider}/login")
def llm_oauth_login(provider: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = llm_client.get(f"/auth/{provider}/login", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/auth/{provider}/status")
def llm_oauth_status(provider: str, principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = llm_client.get(f"/auth/{provider}/status", headers=principal.forwarded_headers)
    return JSONResponse(result)


@router.get("/llm/providers")
def llm_providers(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    result = llm_client.get("/providers", headers=principal.forwarded_headers)
    return JSONResponse(result)


# ── Market Data (proxy to market-data service) ───────────────────────


@router.get("/market-data/{asset}/history")
def get_market_data_history(
    asset: str, request: Request, principal: GatewayPrincipal = Depends(require_principal)
) -> JSONResponse:
    params = dict(request.query_params)
    result = market_data_client.get(f"/candles/{asset}/history", params=params)
    return JSONResponse(result)


@router.get("/market-data/{asset}/latest")
def get_market_data_latest(
    asset: str, principal: GatewayPrincipal = Depends(require_principal)
) -> JSONResponse:
    result = market_data_client.get(f"/candles/{asset}/latest")
    return JSONResponse(result)


# ── System Summary (proxy to orchestrator-agent) ─────────────────────


@router.get("/system/summary")
def system_summary(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    """Proxy to orchestrator-agent for full multi-agent system summary."""
    try:
        response = orchestrator_client.request("GET", "/orchestrator/summary")
        return _proxy_json(response)
    except Exception:
        return JSONResponse({"system_status": "부분 응답", "error": "orchestrator unreachable"})


@router.get("/system/conflicts")
def system_conflicts(principal: GatewayPrincipal = Depends(require_principal)) -> JSONResponse:
    """Proxy to orchestrator-agent for conflict detection."""
    try:
        response = orchestrator_client.request("GET", "/orchestrator/conflicts")
        return _proxy_json(response)
    except Exception:
        return JSONResponse({"conflicts": [], "error": "orchestrator unreachable"})


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
        "orchestrator-agent": _probe_service("orchestrator-agent", settings.orchestrator_agent_base_url),
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


# ── Notification Webhooks ──────────────────────────────────────────────

# In-memory webhook subscribers (later move to DB)
_webhook_subscribers: dict[str, list[str]] = {}  # user_id → [webhook_urls]


@router.post("/notifications/subscribe")
async def subscribe_webhook(
    payload: dict,
    principal: GatewayPrincipal = Depends(require_principal),
) -> dict:
    """Subscribe to BUY/SELL notification webhooks."""
    webhook_url = payload.get("webhook_url")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="webhook_url required")

    user_id = principal.user_id
    if user_id not in _webhook_subscribers:
        _webhook_subscribers[user_id] = []

    if webhook_url not in _webhook_subscribers[user_id]:
        _webhook_subscribers[user_id].append(webhook_url)

    return {"status": "subscribed", "webhook_url": webhook_url}


@router.delete("/notifications/subscribe")
async def unsubscribe_webhook(
    payload: dict,
    principal: GatewayPrincipal = Depends(require_principal),
) -> dict:
    """Unsubscribe from notification webhooks."""
    webhook_url = payload.get("webhook_url")
    user_id = principal.user_id

    if user_id in _webhook_subscribers and webhook_url in _webhook_subscribers[user_id]:
        _webhook_subscribers[user_id].remove(webhook_url)

    return {"status": "unsubscribed"}


@router.get("/notifications/subscriptions")
async def list_subscriptions(principal: GatewayPrincipal = Depends(require_principal)) -> dict:
    """List active webhook subscriptions."""
    return {"webhooks": _webhook_subscribers.get(principal.user_id, [])}


async def trigger_decision_notification(decision: dict) -> None:
    """Send webhook notifications when an agent makes a BUY/SELL decision.

    Called from the agent decision pipeline. Currently broadcasts to all
    subscribers (later: filter by user/asset).
    """
    action = decision.get("action", "HOLD")
    if action == "HOLD":
        return  # only notify on actionable decisions

    asset = decision.get("asset", "")
    price = decision.get("reference_price", 0)
    score = decision.get("signal_score", 0)

    payload = {
        "type": "agent_decision",
        "asset": asset,
        "action": action,
        "price": price,
        "signal_score": score,
        "timestamp": decision.get("timestamp"),
        "message_ko": f"{asset} {action} 시그널 발생 (점수 {score:.2f}, 가격 ${price:,.0f})",
    }

    # Broadcast to all subscribers
    async with httpx.AsyncClient(timeout=5.0) as client:
        for user_id, webhooks in _webhook_subscribers.items():
            for webhook_url in webhooks:
                try:
                    await client.post(webhook_url, json=payload)
                except Exception:
                    pass  # silent fail, don't block agent
