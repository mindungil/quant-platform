import os
import logging
import time
from collections import defaultdict

import redis
from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.core.tokens import issue_access_token, issue_for_profile, refresh_access_token, verify_access_token
from app.db.repository import auth_repository
from app.models.auth import (
    AutomationUpdateRequest,
    BootstrapAdminResponse,
    RefreshTokenRequest,
    RoleUpdateRequest,
    TokenIssueRequest,
    TokenIssueResponse,
    TokenVerificationRequest,
    TokenVerificationResponse,
    UserLoginRequest,
    UserProfile,
    UserRegistrationRequest,
)
from shared.health import check_sql, health_payload
from shared.internal_admin import require_internal_admin

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory rate limiter for login / register brute-force protection
# ---------------------------------------------------------------------------
_login_attempts: dict[str, list[float]] = defaultdict(list)
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW = 300  # 5 minutes

_register_attempts: dict[str, list[float]] = defaultdict(list)
_MAX_REGISTER_ATTEMPTS = 3
_REGISTER_WINDOW = 3600  # 1 hour

_automation_toggle_attempts: dict[str, list[float]] = defaultdict(list)
_MAX_AUTOMATION_TOGGLES = 20
_AUTOMATION_WINDOW = 3600  # 20 toggles per hour per user is plenty


def _check_rate(store: dict[str, list[float]], key: str, max_attempts: int, window: int) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    now = time.time()
    attempts = store[key]
    store[key] = [t for t in attempts if now - t < window]
    return len(store[key]) < max_attempts


def _record_attempt(store: dict[str, list[float]], key: str) -> None:
    store[key].append(time.time())


_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        url = getattr(settings, "redis_url", None) or os.getenv("REDIS_URL", "redis://redis:6379/0")
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
    return _redis_client


def _require_bootstrap_token(x_bootstrap_token: str | None) -> None:
    if x_bootstrap_token != settings.bootstrap_admin_token:
        raise HTTPException(status_code=403, detail="forbidden")


@router.get("/health")
def health() -> dict:
    return health_payload(
        "auth-service",
        {
            "postgres": check_sql("postgres", settings.postgres_url),
        },
    )


@router.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/auth/token", response_model=TokenIssueResponse)
def create_token(
    payload: TokenIssueRequest,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> TokenIssueResponse:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return issue_access_token(payload)


@router.post("/auth/register", response_model=UserProfile)
def register(payload: UserRegistrationRequest) -> UserProfile:
    if not _check_rate(_register_attempts, payload.email.lower(), _MAX_REGISTER_ATTEMPTS, _REGISTER_WINDOW):
        raise HTTPException(status_code=429, detail="too_many_register_attempts")
    _record_attempt(_register_attempts, payload.email.lower())
    try:
        return auth_repository.register(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/auth/login", response_model=TokenIssueResponse)
def login(payload: UserLoginRequest) -> TokenIssueResponse:
    if not _check_rate(_login_attempts, payload.email.lower(), _MAX_LOGIN_ATTEMPTS, _LOGIN_WINDOW):
        raise HTTPException(status_code=429, detail="too_many_login_attempts")
    _record_attempt(_login_attempts, payload.email.lower())
    profile = auth_repository.login(payload.email, payload.password)
    if profile is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    return issue_for_profile(profile)


@router.post("/auth/refresh", response_model=TokenIssueResponse)
def refresh(payload: RefreshTokenRequest) -> TokenIssueResponse:
    refreshed = refresh_access_token(payload)
    if refreshed is None:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")
    return refreshed


@router.post("/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    """Revoke access token + halt this user's automation.

    Three actions, all idempotent:
      1. Token → Redis blacklist (TTL matches token expiry)
      2. automation_enabled → False (next bridge tick won't fan out signals to them)
      3. order-service POST /orders/{user_id}/logout-cancel (cancel any
         live orders — user can't manually intervene without a session)

    Failures in 2/3 are logged but do NOT block the logout response.
    The blacklist alone (1) is the correctness-critical step.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="missing_token")
    token = authorization.removeprefix("Bearer ").strip()
    user_id: str | None = None
    try:
        import hashlib
        result = verify_access_token(token)
        user_id = result.claims.sub
        ttl = max(int(result.claims.exp - time.time()), 0) if result.claims.exp else 3600
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        _get_redis().setex(f"token_blacklist:{token_hash}", ttl, "revoked")
    except Exception:
        return {"status": "logged_out"}  # graceful even if token invalid

    # Best-effort halt of the user's automation footprint.
    if user_id:
        try:
            auth_repository.update_automation_enabled(user_id, False)
            logging.getLogger("auth-service.audit").info(
                "automation_toggle user_id=%s enabled=False source=logout",
                user_id,
            )
        except Exception:
            pass  # logged via repository; don't block logout
        _trigger_logout_cancel(user_id)
    return {"status": "logged_out"}


def _trigger_logout_cancel(user_id: str) -> None:
    """Call order-service to cancel this user's open orders. Best-effort.

    Internal-admin signed call so order-service trusts us. Short timeout
    so a slow/down order-service can't block the logout HTTP response.
    """
    import httpx
    from shared.internal_admin import build_internal_admin_headers
    path = f"/orders/{user_id}/logout-cancel"
    try:
        headers = build_internal_admin_headers(
            settings.internal_admin_secret,
            actor_user_id="auth-service",
            path=path,
        )
        with httpx.Client(timeout=settings.logout_cancel_timeout_seconds) as client:
            client.post(f"{settings.order_service_base_url.rstrip('/')}{path}", headers=headers)
    except Exception:
        # Not fatal — user is logged out, automation_enabled is false. Worst
        # case: previously-submitted orders complete naturally on the exchange.
        pass


@router.post("/auth/verify", response_model=TokenVerificationResponse)
def verify_token(payload: TokenVerificationRequest) -> TokenVerificationResponse:
    return verify_access_token(payload.token)


@router.get("/auth/me", response_model=UserProfile)
def me(
    x_user_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> UserProfile:
    user_id = x_user_id
    if user_id is None and authorization:
        token = authorization.removeprefix("Bearer ").strip()
        try:
            result = verify_access_token(token)
            user_id = result.claims.sub
        except Exception:
            raise HTTPException(status_code=401, detail="invalid_token")
    if user_id is None:
        raise HTTPException(status_code=401, detail="missing_user_context")
    profile = auth_repository.get_by_user_id(user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return profile


@router.patch("/auth/me/automation", response_model=UserProfile)
def update_my_automation(
    payload: AutomationUpdateRequest,
    request: Request,
    x_user_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> UserProfile:
    """User-self toggle for automated trading.

    Auth: prefer JWT (verified). X-User-ID accepted only as fallback for
    api-gateway forwarding (gateway already validated upstream).

    Rate-limited to 20 toggles/hour per user — automated trading flag
    flapping has no legitimate use case at high frequency, and unbounded
    toggling would write-storm the DB.

    Every successful toggle is logged at INFO with user_id + new state +
    actor source for compliance / forensics.
    """
    # Prefer the verified JWT subject — only fall back to X-User-ID when no
    # token is supplied (e.g. an internal-admin signed call from another
    # service that has already verified the user).
    auth_source = "unknown"
    user_id: str | None = None
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
        try:
            result = verify_access_token(token)
            user_id = result.claims.sub
            auth_source = "jwt"
        except Exception:
            raise HTTPException(status_code=401, detail="invalid_token")
    elif x_user_id:
        user_id = x_user_id
        auth_source = "header"
    if user_id is None:
        raise HTTPException(status_code=401, detail="missing_user_context")

    if not _check_rate(_automation_toggle_attempts, user_id, _MAX_AUTOMATION_TOGGLES, _AUTOMATION_WINDOW):
        raise HTTPException(status_code=429, detail="too_many_automation_toggles")
    _record_attempt(_automation_toggle_attempts, user_id)

    profile = auth_repository.update_automation_enabled(user_id, payload.enabled)
    if profile is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    # Compliance audit: who flipped what, from where.
    logging.getLogger("auth-service.audit").info(
        "automation_toggle user_id=%s enabled=%s source=%s ip=%s",
        user_id, payload.enabled, auth_source,
        request.client.host if request.client else "unknown",
    )
    return profile


@router.post("/admin/bootstrap", response_model=BootstrapAdminResponse)
def bootstrap_admin(x_bootstrap_token: str | None = Header(default=None)) -> BootstrapAdminResponse:
    _require_bootstrap_token(x_bootstrap_token)
    result = auth_repository.bootstrap_admin()
    if result is None:
        raise HTTPException(status_code=400, detail="bootstrap_admin_not_configured")
    profile, created = result
    return BootstrapAdminResponse(user=profile, created=created)


@router.get("/admin/users", response_model=list[UserProfile])
def list_users(
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> list[UserProfile]:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    return auth_repository.list_users()


@router.patch("/admin/users/{user_id}/roles", response_model=UserProfile)
def update_user_roles(
    user_id: str,
    payload: RoleUpdateRequest,
    request: Request,
    x_internal_actor_user_id: str | None = Header(default=None),
    x_internal_admin_timestamp: str | None = Header(default=None),
    x_internal_admin_signature: str | None = Header(default=None),
) -> UserProfile:
    require_internal_admin(
        request=request,
        secret=settings.internal_admin_secret,
        actor_user_id=x_internal_actor_user_id,
        timestamp=x_internal_admin_timestamp,
        signature=x_internal_admin_signature,
        ttl_seconds=settings.admin_header_ttl_seconds,
    )
    profile = auth_repository.update_roles(user_id, payload.roles)
    if profile is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return profile
