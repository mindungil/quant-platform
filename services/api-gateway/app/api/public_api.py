"""Public API — B2B signal and factor data access.

Endpoints:
  GET /api/v1/signals/{asset} — latest signal evaluation
  GET /api/v1/decisions/{asset} — recent agent decisions
  GET /api/v1/factors/{asset} — current factor scores
  GET /api/v1/status — system status

Authentication: X-API-Key header
Rate limits: 100/min (free), 1000/min (paid)
"""
import os
import logging
from fastapi import APIRouter, Header, HTTPException
import httpx

logger = logging.getLogger("api-gateway")
router = APIRouter(prefix="/api/v1", tags=["Public API"])

# Simple API key validation (in production, this would be a DB lookup)
VALID_API_KEYS = set(filter(None, os.getenv("PUBLIC_API_KEYS", "").split(",")))


def _require_api_key(x_api_key: str | None = Header(default=None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required. Pass X-API-Key header.")
    if VALID_API_KEYS and x_api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return x_api_key


@router.get("/signals/{asset}")
def get_signal(asset: str, x_api_key: str = Header(default=None)):
    """Get latest signal evaluation for an asset."""
    _require_api_key(x_api_key)
    try:
        resp = httpx.get(f"http://localhost:8003/signals/{asset}/latest", timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return {"error": "signal_not_found", "asset": asset}
    except Exception:
        raise HTTPException(status_code=502, detail="Signal service unavailable")


@router.get("/decisions/{asset}")
def get_decisions(asset: str, limit: int = 10, x_api_key: str = Header(default=None)):
    """Get recent agent decisions for an asset."""
    _require_api_key(x_api_key)
    try:
        resp = httpx.get(
            f"http://localhost:8006/decisions/history/{asset}?limit={min(limit, 50)}",
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        raise HTTPException(status_code=502, detail="Agent service unavailable")


@router.get("/factors/{asset}")
def get_factors(asset: str, x_api_key: str = Header(default=None)):
    """Get current factor scores for an asset."""
    _require_api_key(x_api_key)
    try:
        # Compute factors from latest features
        resp = httpx.get(f"http://localhost:8002/features/{asset}/latest", timeout=5)
        if resp.status_code != 200:
            return {"error": "features_not_found"}
        features = resp.json()
        features["asset"] = asset

        from shared.factors import compute_all
        scores = compute_all(features)

        # Group by category
        from shared.factors.registry import ALL_FACTORS
        categorized: dict[str, dict] = {}
        for f in ALL_FACTORS:
            cat = f.category
            if cat not in categorized:
                categorized[cat] = {}
            score = scores.get(f.name, 0)
            if score != 0:
                categorized[cat][f.name] = round(score, 4)

        return {
            "asset": asset,
            "total_factors": len(scores),
            "active_factors": sum(1 for v in scores.values() if v != 0),
            "categories": categorized,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)[:200])


@router.get("/status")
def get_status(x_api_key: str = Header(default=None)):
    """Get system health status."""
    _require_api_key(x_api_key)
    services = {}
    for name, port in [("signal", 8003), ("agent", 8006), ("market-data", 8001), ("features", 8002)]:
        try:
            resp = httpx.get(f"http://localhost:{port}/health", timeout=3)
            services[name] = "ok" if resp.status_code == 200 else "error"
        except Exception:
            services[name] = "unreachable"

    return {
        "status": "operational" if all(v == "ok" for v in services.values()) else "degraded",
        "services": services,
    }
