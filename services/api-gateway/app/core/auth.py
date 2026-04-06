import hmac
import time
from hashlib import sha256

from fastapi import Header, HTTPException
import jwt

from app.core.config import settings
from app.core.rate_limiter import check_rate_limit
from app.models.auth import GatewayPrincipal
from shared.request_context import current_request_headers


def require_principal(authorization: str | None = Header(default=None)) -> GatewayPrincipal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer_token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc

    user_id = payload["sub"]
    roles = payload.get("roles", [])

    # Rate limiting based on user tier
    tier = "admin" if "admin" in roles else ("pro" if "pro" in roles else "user")
    allowed, remaining = check_rate_limit(user_id, tier)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")

    return GatewayPrincipal(
        user_id=user_id,
        email=payload.get("email"),
        roles=roles,
        forwarded_headers={"X-User-ID": user_id, **current_request_headers()},
    )


def require_role(role: str):
    def wrapper(authorization: str | None = Header(default=None)) -> GatewayPrincipal:
        principal = require_principal(authorization)
        if role not in principal.roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return wrapper


def build_internal_admin_headers(principal: GatewayPrincipal, path: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    message = f"{principal.user_id}:{timestamp}:{path}"
    signature = hmac.new(
        settings.internal_admin_secret.encode("utf-8"),
        message.encode("utf-8"),
        sha256,
    ).hexdigest()
    return {
        **current_request_headers(),
        **principal.forwarded_headers,
        "X-Internal-Actor-User-ID": principal.user_id,
        "X-Internal-Admin-Timestamp": timestamp,
        "X-Internal-Admin-Signature": signature,
    }
