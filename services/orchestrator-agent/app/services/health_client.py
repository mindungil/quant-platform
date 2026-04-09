from __future__ import annotations

import httpx

_TIMEOUT = 5.0  # seconds


def check_service_health(base_url: str, *, timeout: float = _TIMEOUT) -> dict:
    """Call GET /health on a downstream service and return a status dict."""
    try:
        resp = httpx.get(f"{base_url}/health", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return {"url": base_url, "status": data.get("status", "ok"), "healthy": True}
    except httpx.HTTPStatusError as exc:
        return {"url": base_url, "status": f"http_{exc.response.status_code}", "healthy": False}
    except Exception as exc:
        return {"url": base_url, "status": str(type(exc).__name__), "healthy": False}


def fetch_agent_decisions(base_url: str, *, timeout: float = _TIMEOUT) -> dict | None:
    """Attempt to retrieve the latest decision from an agent service.

    Returns the JSON body on success, or None on failure.
    """
    try:
        resp = httpx.get(f"{base_url}/decisions/latest", timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None
