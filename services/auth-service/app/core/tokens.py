import logging
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from uuid import uuid4

import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)
from app.db.repository import auth_repository
from app.models.auth import (
    RefreshTokenRequest,
    TokenClaims,
    TokenIssueRequest,
    TokenIssueResponse,
    TokenVerificationResponse,
    UserProfile,
)


def issue_access_token(payload: TokenIssueRequest) -> TokenIssueResponse:
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_expiration_minutes)
    claims = TokenClaims(
        sub=payload.user_id,
        email=payload.email,
        roles=payload.roles,
        plan=payload.plan,
        iat=int(issued_at.timestamp()),
        exp=int(expires_at.timestamp()),
        iss=settings.jwt_issuer,
    )
    token = jwt.encode(claims.model_dump(), settings.jwt_secret, algorithm=settings.jwt_algorithm)
    refresh_token = str(uuid4())
    auth_repository.store_refresh_token(payload.user_id, refresh_token)
    return TokenIssueResponse(
        access_token=token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_at=expires_at,
        claims=claims,
    )


def verify_access_token(token: str) -> TokenVerificationResponse:
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
    )
    # Check if token has been revoked (blacklisted)
    try:
        import os
        import redis as _redis
        url = getattr(settings, "redis_url", None) or os.getenv("REDIS_URL", "redis://redis:6379/0")
        r = _redis.Redis.from_url(url, decode_responses=True)
        if r.exists(f"token_blacklist:{token[:32]}"):
            raise jwt.InvalidTokenError("token_revoked")
    except jwt.InvalidTokenError:
        raise
    except Exception as exc:
        logger.warning("token_blacklist_check_failed", extra={"error": str(exc)[:100]})
        # Fail open — token expiry is the primary protection

    claims = TokenClaims(**payload)
    return TokenVerificationResponse(valid=True, claims=claims)


def issue_for_profile(profile: UserProfile) -> TokenIssueResponse:
    return issue_access_token(
        TokenIssueRequest(
            user_id=profile.user_id,
            email=profile.email,
            roles=profile.roles,
            plan=profile.plan,
        )
    )


def refresh_access_token(payload: RefreshTokenRequest) -> TokenIssueResponse | None:
    profile = auth_repository.consume_refresh_token(payload.refresh_token)
    if profile is None:
        return None
    return issue_for_profile(profile)
