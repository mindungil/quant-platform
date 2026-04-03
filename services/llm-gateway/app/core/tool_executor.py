"""Tool Executor — LLM 도구 호출을 내부 마이크로서비스 HTTP로 실행.

OpenCode/Codex 패턴: tool_name + arguments → HTTP 요청 → 결과 반환.
각 도구는 해당 서비스의 REST API 엔드포인트에 매핑.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger("llm-gateway")

_client = httpx.AsyncClient(timeout=30.0)

# ── Service base URLs ────────────────────────────────────────────────
_URLS = {
    "market_data": lambda: settings.market_data_base_url,
    "feature_store": lambda: settings.feature_store_base_url,
    "signal_service": lambda: settings.signal_service_base_url,
    "memory_service": lambda: settings.memory_service_base_url,
    "strategy_registry": lambda: settings.strategy_registry_base_url,
    "backtest_service": lambda: settings.backtest_service_base_url,
    "risk_service": lambda: settings.risk_service_base_url,
    "order_service": lambda: settings.order_service_base_url,
    "portfolio_service": lambda: settings.portfolio_service_base_url,
}


def _url(service: str, path: str) -> str:
    return f"{_URLS[service]().rstrip('/')}{path}"


# ── Tool Handlers ────────────────────────────────────────────────────

async def _get_market_data(args: dict, user_id: str) -> dict:
    asset = args["asset"]
    limit = args.get("limit", 50)
    resp = await _client.get(_url("market_data", f"/candles/{asset}/history"), params={"limit": limit})
    resp.raise_for_status()
    candles = resp.json()
    if isinstance(candles, list) and len(candles) > 5:
        latest = candles[:3]
        summary = {
            "total_candles": len(candles),
            "latest_3": latest,
            "price_range": {
                "high": max(c.get("high", 0) for c in candles),
                "low": min(c.get("low", float("inf")) for c in candles),
            },
            "latest_close": candles[0].get("close") if candles else None,
        }
        return summary
    return {"candles": candles}


async def _get_features(args: dict, user_id: str) -> dict:
    asset = args["asset"]
    resp = await _client.get(_url("feature_store", f"/features/{asset}/latest"))
    resp.raise_for_status()
    return resp.json()


async def _get_signal(args: dict, user_id: str) -> dict:
    asset = args["asset"]
    resp = await _client.get(
        _url("signal_service", f"/signals/{asset}/latest"),
        headers={"x-user-id": user_id},
    )
    resp.raise_for_status()
    return resp.json()


async def _get_portfolio(args: dict, user_id: str) -> dict:
    uid = args.get("user_id", user_id)
    resp = await _client.get(_url("portfolio_service", f"/portfolio/{uid}"))
    resp.raise_for_status()
    return resp.json()


async def _detect_regime(args: dict, user_id: str) -> dict:
    """Get features then run regime detection locally."""
    asset = args["asset"]
    resp = await _client.get(_url("feature_store", f"/features/{asset}/latest"))
    resp.raise_for_status()
    features = resp.json()

    from shared.regime import detect_regime, suggest_formula_type
    regime = detect_regime(features)
    suggestion = suggest_formula_type(regime)
    return {
        "asset": asset,
        "regime": {
            "trend_strength": regime.trend_strength,
            "volatility": regime.volatility,
            "momentum": regime.momentum,
            "label": regime.label,
            "confidence": regime.confidence,
        },
        "suggested_formula_type": suggestion,
    }


async def _search_memory(args: dict, user_id: str) -> dict:
    body = {
        "user_id": user_id,
        "asset": args["asset"],
        "asset_type": "crypto",
        "signal_score": args.get("signal_score", 0.0),
        "action": args.get("action"),
        "top_k": args.get("top_k", 5),
    }
    resp = await _client.post(
        _url("memory_service", "/memory/search"),
        json=body,
        headers={"x-user-id": user_id},
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])
    return {
        "total_results": len(items),
        "items": [
            {
                "score": it.get("score"),
                "asset": it.get("record", {}).get("asset"),
                "action": it.get("record", {}).get("action"),
                "signal_score": it.get("record", {}).get("signal_score"),
                "reasoning": it.get("record", {}).get("reasoning", "")[:200],
                "formula_name": it.get("record", {}).get("formula_name"),
                "regime_label": it.get("record", {}).get("regime_label"),
                "trade_outcome": it.get("record", {}).get("trade_outcome"),
            }
            for it in items[:5]
        ],
    }


async def _search_formula_outcomes(args: dict, user_id: str) -> dict:
    body = {
        "regime_label": args["regime_label"],
        "asset": args.get("asset"),
        "formula_name": args.get("formula_name"),
        "top_k": args.get("top_k", 10),
    }
    resp = await _client.post(
        _url("memory_service", "/memory/search/formula-outcomes"),
        json=body,
        headers={"x-user-id": user_id},
    )
    resp.raise_for_status()
    return resp.json()


async def _get_risk_assessment(args: dict, user_id: str) -> dict:
    body = {
        "user_id": user_id,
        "asset": args["asset"],
        "requested_notional": args["requested_notional"],
        "max_notional": args.get("max_notional", 10000.0),
        "current_drawdown": args.get("current_drawdown", 0.0),
        "current_exposure": args.get("current_exposure", 0.0),
        "exposure_limit": args.get("exposure_limit", 1.0),
    }
    resp = await _client.post(_url("risk_service", "/risk/approve"), json=body)
    resp.raise_for_status()
    return resp.json()


async def _evaluate_formula(args: dict, user_id: str) -> dict:
    body = {
        "strategy_id": args["strategy_id"],
        "weights": args["weights"],
        "asset": args.get("asset", "BTCUSDT"),
        "sample_size": args.get("sample_size", 500),
    }
    resp = await _client.post(_url("backtest_service", "/backtests/run"), json=body)
    resp.raise_for_status()
    job = resp.json()
    # Poll for completion (backtest is async)
    job_id = job.get("job_id")
    if job_id and job.get("status") != "COMPLETED":
        for _ in range(10):
            await _async_sleep(1.0)
            poll = await _client.get(_url("backtest_service", f"/backtests/{job_id}"))
            poll.raise_for_status()
            job = poll.json()
            if job.get("status") in ("COMPLETED", "FAILED"):
                break
    return job


async def _place_order(args: dict, user_id: str) -> dict:
    body = {
        "user_id": user_id,
        "exchange": "binance",
        "asset": args["asset"],
        "side": args["side"],
        "quantity": args["quantity"],
        "requested_notional": args["requested_notional"],
        "max_notional": args.get("max_notional", 10000.0),
        "current_drawdown": 0.0,
        "shadow_mode": True,
        "stop_loss_pct": args.get("stop_loss_pct"),
        "take_profit_pct": args.get("take_profit_pct"),
    }
    resp = await _client.post(_url("order_service", "/orders"), json=body)
    resp.raise_for_status()
    result = resp.json()
    return {
        "order_id": result.get("order_id"),
        "status": result.get("status"),
        "asset": result.get("asset"),
        "side": result.get("side"),
        "quantity": result.get("quantity"),
        "shadow_mode": result.get("shadow_mode"),
        "risk_reason": result.get("risk_reason"),
    }


async def _store_memory(args: dict, user_id: str) -> dict:
    body = {
        "user_id": user_id,
        "asset": args["asset"],
        "asset_type": "crypto",
        "signal_score": args["signal_score"],
        "action": args["action"],
        "reasoning": args["reasoning"],
        "formula_name": args.get("formula_name"),
        "regime_label": args.get("regime_label"),
    }
    resp = await _client.post(
        _url("memory_service", "/memory/record"),
        json=body,
        headers={"x-user-id": user_id},
    )
    resp.raise_for_status()
    return resp.json()


async def _list_formulas(args: dict, user_id: str) -> dict:
    from shared.formulas.registry import formula_registry
    regime_filter = args.get("regime")
    if regime_filter:
        formulas = formula_registry.get_for_regime(regime_filter)
    else:
        formulas = [formula_registry.get(n) for n in formula_registry.list_names()]
    return {
        "formulas": [
            {
                "name": f.name,
                "description": f.description,
                "best_regime": f.best_regime,
            }
            for f in formulas
            if f is not None
        ],
    }


async def _list_strategies(args: dict, user_id: str) -> dict:
    params: dict[str, str] = {}
    if args.get("asset_type"):
        params["asset_type"] = args["asset_type"]
    if args.get("status"):
        params["status"] = args["status"]
    resp = await _client.get(
        _url("strategy_registry", "/strategies"),
        params=params,
        headers={"x-user-id": user_id},
    )
    resp.raise_for_status()
    strategies = resp.json()
    return {
        "total": len(strategies),
        "strategies": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "asset_type": s.get("asset_type"),
                "status": s.get("status"),
                "version": s.get("version"),
            }
            for s in (strategies if isinstance(strategies, list) else [])
        ],
    }


async def _register_formula(args: dict, user_id: str) -> dict:
    body = {
        "name": args["name"],
        "asset_type": args.get("asset_type", "crypto"),
        "config": args["config"],
    }
    resp = await _client.post(_url("strategy_registry", "/models"), json=body)
    resp.raise_for_status()
    return resp.json()


async def _promote_formula(args: dict, user_id: str) -> dict:
    model_id = args["model_id"]
    resp = await _client.post(_url("strategy_registry", f"/models/{model_id}/promote"))
    resp.raise_for_status()
    return resp.json()


async def _get_trading_rules(args: dict, user_id: str) -> dict:
    """Runbook: 매매 규칙 (DB 기반 동적 로드 가능, 현재는 정적)."""
    # Fetch risk settings for the user
    try:
        resp = await _client.get(_url("risk_service", f"/risk/settings/{user_id}"))
        resp.raise_for_status()
        risk_settings = resp.json()
    except Exception:
        risk_settings = {}

    return {
        "rules": [
            {
                "rule": "리스크 평가 필수",
                "detail": "주문 전 반드시 get_risk_assessment를 호출하여 리스크 승인을 받으세요.",
            },
            {
                "rule": "포지션 한도",
                "detail": f"단일 자산 최대 포지션: 포트폴리오의 {risk_settings.get('max_single_asset_weight', 0.30) * 100:.0f}%",
            },
            {
                "rule": "손절/익절 필수",
                "detail": "모든 주문에 stop_loss_pct와 take_profit_pct를 설정하세요.",
            },
            {
                "rule": "최소 주문 금액",
                "detail": "최소 주문 금액은 $10 (requested_notional >= 10).",
            },
            {
                "rule": "드로다운 제한",
                "detail": f"경고 드로다운: {risk_settings.get('warning_drawdown', 0.05) * 100:.0f}%, 청산 드로다운: {risk_settings.get('liquidate_drawdown', 0.10) * 100:.0f}%",
            },
            {
                "rule": "중복 방지",
                "detail": "동일 자산에 60초 이내 중복 주문을 피하세요.",
            },
            {
                "rule": "섀도우 모드",
                "detail": "현재 기본 모드는 shadow(시뮬레이션)입니다. 실제 매매는 관리자 승인이 필요합니다.",
            },
        ],
        "risk_settings": risk_settings,
    }


async def _get_formula_guide(args: dict, user_id: str) -> dict:
    """Runbook: 레짐별 공식 선택 가이드."""
    from shared.formulas.registry import formula_registry

    guide = {
        "trending": {
            "description": "강한 추세 시장 (ADX >= 25)",
            "recommended": ["ema_cross", "macd_histogram", "stochastic_momentum"],
            "reason": "추세 추종 전략이 높은 수익률을 보입니다.",
        },
        "sideways": {
            "description": "횡보 시장 (ADX < 25, 낮은 변동성)",
            "recommended": ["bollinger_reversion", "vwap_reversion", "rsi_divergence"],
            "reason": "평균 회귀 전략이 적합합니다. 밴드 상/하단에서 반전을 노립니다.",
        },
        "breakout": {
            "description": "변동성 축소 후 돌파 (볼린저 스퀴즈)",
            "recommended": ["volatility_breakout"],
            "reason": "축소된 변동성이 확장될 때 큰 움직임을 포착합니다.",
        },
        "high_volatility": {
            "description": "높은 변동성 시장",
            "recommended": ["composite_adaptive"],
            "reason": "단일 공식 리스크가 높으므로 복합 적응형을 권장합니다.",
        },
    }

    all_formulas = [
        {"name": f.name, "description": f.description, "best_regime": f.best_regime}
        for n in formula_registry.list_names()
        if (f := formula_registry.get(n)) is not None
    ]

    return {"guide": guide, "available_formulas": all_formulas}


async def _full_market_analysis(args: dict, user_id: str) -> dict:
    """Composite: 종합 시장 분석 파이프라인."""
    asset = args["asset"]
    results: dict[str, Any] = {"asset": asset}

    # 1. Market data (latest price)
    try:
        resp = await _client.get(_url("market_data", f"/candles/{asset}/latest"))
        resp.raise_for_status()
        results["latest_candle"] = resp.json()
    except Exception as e:
        results["latest_candle"] = {"error": str(e)[:100]}

    # 2. Features
    try:
        resp = await _client.get(_url("feature_store", f"/features/{asset}/latest"))
        resp.raise_for_status()
        features = resp.json()
        results["features"] = features
    except Exception as e:
        features = {}
        results["features"] = {"error": str(e)[:100]}

    # 3. Regime detection
    try:
        from shared.regime import detect_regime, suggest_formula_type
        regime = detect_regime(features)
        suggestion = suggest_formula_type(regime)
        results["regime"] = {
            "label": regime.label,
            "trend_strength": regime.trend_strength,
            "volatility": regime.volatility,
            "momentum": regime.momentum,
            "confidence": regime.confidence,
            "suggested_formula_type": suggestion,
        }
    except Exception as e:
        results["regime"] = {"error": str(e)[:100]}

    # 4. Signal
    try:
        resp = await _client.get(
            _url("signal_service", f"/signals/{asset}/latest"),
            headers={"x-user-id": user_id},
        )
        resp.raise_for_status()
        results["signal"] = resp.json()
    except Exception as e:
        results["signal"] = {"error": str(e)[:100]}

    # 5. Memory (recent similar decisions)
    try:
        signal_score = results.get("signal", {}).get("signal_score", 0.0)
        resp = await _client.post(
            _url("memory_service", "/memory/search"),
            json={"user_id": user_id, "asset": asset, "asset_type": "crypto", "signal_score": signal_score, "top_k": 3},
            headers={"x-user-id": user_id},
        )
        resp.raise_for_status()
        mem = resp.json()
        results["recent_memories"] = len(mem.get("items", []))
    except Exception as e:
        results["recent_memories"] = 0

    # 6. Formula recommendation
    try:
        regime_label = results.get("regime", {}).get("label", "unknown")
        from shared.formulas.registry import formula_registry
        regime_type = results.get("regime", {}).get("suggested_formula_type", "any")
        recommended = formula_registry.get_for_regime(regime_type)
        results["recommended_formulas"] = [
            {"name": f.name, "best_regime": f.best_regime}
            for f in recommended[:3]
        ]
    except Exception as e:
        results["recommended_formulas"] = []

    return results


# ── Sleep helper ─────────────────────────────────────────────────────
import asyncio

async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


# ── Dispatcher ───────────────────────────────────────────────────────

_HANDLERS: dict[str, Any] = {
    "get_market_data": _get_market_data,
    "get_features": _get_features,
    "get_signal": _get_signal,
    "get_portfolio": _get_portfolio,
    "detect_regime": _detect_regime,
    "search_memory": _search_memory,
    "search_formula_outcomes": _search_formula_outcomes,
    "get_risk_assessment": _get_risk_assessment,
    "evaluate_formula": _evaluate_formula,
    "place_order": _place_order,
    "store_memory": _store_memory,
    "list_formulas": _list_formulas,
    "list_strategies": _list_strategies,
    "register_formula": _register_formula,
    "promote_formula": _promote_formula,
    "get_trading_rules": _get_trading_rules,
    "get_formula_guide": _get_formula_guide,
    "full_market_analysis": _full_market_analysis,
}


async def execute_tool(tool_name: str, arguments: dict, user_id: str) -> str:
    """Execute a tool and return JSON-serialized result.

    Returns a JSON string suitable for LLM tool_result content.
    On error, returns a JSON error object so the LLM can reason about it.
    """
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return json.dumps({"error": f"unknown tool: {tool_name}"}, ensure_ascii=False)

    start = time.monotonic()
    try:
        result = await handler(arguments, user_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("tool_executed", extra={
            "tool": tool_name, "elapsed_ms": f"{elapsed_ms:.0f}", "user_id": user_id,
        })
        return json.dumps(result, ensure_ascii=False, default=str)
    except httpx.HTTPStatusError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.warning("tool_http_error", extra={
            "tool": tool_name, "status": exc.response.status_code,
            "body": exc.response.text[:200], "elapsed_ms": f"{elapsed_ms:.0f}",
        })
        return json.dumps({
            "error": f"서비스 오류 ({exc.response.status_code})",
            "detail": exc.response.text[:200],
        }, ensure_ascii=False)
    except httpx.ConnectError:
        return json.dumps({"error": f"{tool_name} 서비스에 연결할 수 없습니다. 서비스가 실행 중인지 확인하세요."}, ensure_ascii=False)
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error("tool_execution_failed", extra={
            "tool": tool_name, "error": str(exc)[:200], "elapsed_ms": f"{elapsed_ms:.0f}",
        })
        return json.dumps({"error": str(exc)[:300]}, ensure_ascii=False)
