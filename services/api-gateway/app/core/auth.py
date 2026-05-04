from fastapi import Header, HTTPException
import httpx

from app.core.config import settings
from app.core.rate_limiter import check_rate_limit
from app.models.auth import GatewayPrincipal
from shared.internal_admin import build_internal_admin_headers as _shared_build_internal_admin_headers
from shared.request_context import current_request_headers


def _verify_via_auth_service(token: str) -> dict:
    response = httpx.post(
        f"{settings.auth_service_base_url.rstrip('/')}/auth/verify",
        json={"token": token},
        timeout=5.0,
    )
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="invalid_token")
    response.raise_for_status()
    payload = response.json()
    if not payload.get("valid"):
        raise HTTPException(status_code=401, detail="invalid_token")
    return payload["claims"]


def require_principal(authorization: str | None = Header(default=None)) -> GatewayPrincipal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer_token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = _verify_via_auth_service(token)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=503, detail="auth_service_unavailable") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="auth_service_unavailable") from exc

    user_id = payload["sub"]
    roles = payload.get("roles", [])
    plan = payload.get("plan", "free")

    # Rate limiting based on user tier (use plan, not roles)
    tier = "admin" if "admin" in roles else plan
    allowed, remaining = check_rate_limit(user_id, tier)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")

    return GatewayPrincipal(
        user_id=user_id,
        email=payload.get("email"),
        roles=roles,
        plan=plan,
        forwarded_headers={"X-User-ID": user_id, "X-Plan": plan, **current_request_headers()},
    )


TIER_FEATURES = {
    "FREE": {
        "can_trade": False,
        "can_automate": False,
        "chat_daily_limit": 5,
        "signal_delay_minutes": 5,
        "max_assets": 0,
        "decisions_limit": 1,
    },
    "PRO": {
        "can_trade": True,
        "can_automate": True,
        "chat_daily_limit": 50,
        "signal_delay_minutes": 0,
        "max_assets": 1,
        "decisions_limit": 100,
    },
    "PREMIUM": {
        "can_trade": True,
        "can_automate": True,
        "chat_daily_limit": 9999,
        "signal_delay_minutes": 0,
        "max_assets": 99,
        "decisions_limit": 9999,
    },
}


def get_tier_features(plan: str) -> dict:
    return TIER_FEATURES.get(plan.upper(), TIER_FEATURES["FREE"])


def check_feature(principal, feature: str) -> bool:
    """Check if user's tier allows a specific feature."""
    plan = getattr(principal, "plan", "FREE") or "FREE"
    features = get_tier_features(plan)
    return features.get(feature, False)


def require_role(role: str):
    def wrapper(authorization: str | None = Header(default=None)) -> GatewayPrincipal:
        principal = require_principal(authorization)
        if role not in principal.roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return wrapper


def build_internal_admin_headers(principal: GatewayPrincipal, path: str) -> dict[str, str]:
    return {
        **current_request_headers(),
        **principal.forwarded_headers,
        **_shared_build_internal_admin_headers(
            settings.internal_admin_secret,
            principal.user_id,
            path,
        ),
    }
