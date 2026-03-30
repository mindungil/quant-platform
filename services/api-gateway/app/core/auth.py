from fastapi import Header, HTTPException
import jwt

from app.core.config import settings
from app.models.auth import GatewayPrincipal


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

    return GatewayPrincipal(
        user_id=payload["sub"],
        email=payload.get("email"),
        roles=payload.get("roles", []),
        forwarded_headers={"X-User-ID": payload["sub"]},
    )
