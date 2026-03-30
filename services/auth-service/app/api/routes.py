from fastapi import APIRouter, Header, HTTPException

from app.core.tokens import issue_access_token, issue_for_profile, refresh_access_token, verify_access_token
from app.db.repository import auth_repository
from app.models.auth import (
    RefreshTokenRequest,
    TokenIssueRequest,
    TokenIssueResponse,
    TokenVerificationRequest,
    TokenVerificationResponse,
    UserLoginRequest,
    UserProfile,
    UserRegistrationRequest,
)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/auth/token", response_model=TokenIssueResponse)
def create_token(payload: TokenIssueRequest) -> TokenIssueResponse:
    return issue_access_token(payload)


@router.post("/auth/register", response_model=UserProfile)
def register(payload: UserRegistrationRequest) -> UserProfile:
    return auth_repository.register(payload)


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
def me(x_user_id: str | None = Header(default=None)) -> UserProfile:
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="missing_user_context")
    profile = auth_repository.get_by_user_id(x_user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return profile
