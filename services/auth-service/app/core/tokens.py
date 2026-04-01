from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt

from app.core.config import settings
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
    claims = TokenClaims(**payload)
    return TokenVerificationResponse(valid=True, claims=claims)


def issue_for_profile(profile: UserProfile) -> TokenIssueResponse:
    return issue_access_token(
        TokenIssueRequest(
            user_id=profile.user_id,
            email=profile.email,
            roles=profile.roles,
        )
    )


def refresh_access_token(payload: RefreshTokenRequest) -> TokenIssueResponse | None:
    profile = auth_repository.consume_refresh_token(payload.refresh_token)
    if profile is None:
        return None
    return issue_for_profile(profile)
