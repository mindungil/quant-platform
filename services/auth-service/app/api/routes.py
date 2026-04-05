import hmac
import time
from hashlib import sha256

from fastapi import APIRouter, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import settings
from app.core.tokens import issue_access_token, issue_for_profile, refresh_access_token, verify_access_token
from app.db.repository import auth_repository
from app.models.auth import (
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

router = APIRouter()


def _require_bootstrap_token(x_bootstrap_token: str | None) -> None:
    if x_bootstrap_token != settings.bootstrap_admin_token:
        raise HTTPException(status_code=403, detail="forbidden")


def _require_internal_admin(
    request: Request,
    x_internal_actor_user_id: str | None,
    x_internal_admin_timestamp: str | None,
    x_internal_admin_signature: str | None,
) -> str:
    if not x_internal_actor_user_id or not x_internal_admin_timestamp or not x_internal_admin_signature:
        raise HTTPException(status_code=403, detail="missing_internal_admin_headers")

    try:
        timestamp = int(x_internal_admin_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid_internal_admin_timestamp") from exc

    now = int(time.time())
    if abs(now - timestamp) > settings.admin_header_ttl_seconds:
        raise HTTPException(status_code=403, detail="expired_internal_admin_signature")

    message = f"{x_internal_actor_user_id}:{x_internal_admin_timestamp}:{request.url.path}"
    expected = hmac.new(
        settings.internal_admin_secret.encode("utf-8"),
        message.encode("utf-8"),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, x_internal_admin_signature):
        raise HTTPException(status_code=403, detail="invalid_internal_admin_signature")
    return x_internal_actor_user_id


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
def create_token(payload: TokenIssueRequest) -> TokenIssueResponse:
    return issue_access_token(payload)


@router.post("/auth/register", response_model=UserProfile)
def register(payload: UserRegistrationRequest) -> UserProfile:
    try:
        return auth_repository.register(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/auth/login", response_model=TokenIssueResponse)
def login(payload: UserLoginRequest) -> TokenIssueResponse:
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
    _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)
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
    _require_internal_admin(request, x_internal_actor_user_id, x_internal_admin_timestamp, x_internal_admin_signature)
    profile = auth_repository.update_roles(user_id, payload.roles)
    if profile is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return profile
