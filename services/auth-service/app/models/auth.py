from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _validate_password_complexity(v: str) -> str:
    if len(v) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다")
    if not any(c.isupper() for c in v):
        raise ValueError("대문자를 하나 이상 포함해야 합니다")
    if not any(c.islower() for c in v):
        raise ValueError("소문자를 하나 이상 포함해야 합니다")
    if not any(c.isdigit() for c in v):
        raise ValueError("숫자를 하나 이상 포함해야 합니다")
    return v


class UserRegistrationRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1)
    plan: str = "free"

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return _validate_password_complexity(v)


class UserLoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)


class UserProfile(BaseModel):
    user_id: str = Field(min_length=1)
    email: str
    display_name: str
    plan: str
    roles: list[str] = Field(default_factory=list)
    automation_enabled: bool = False


class RoleUpdateRequest(BaseModel):
    roles: list[str] = Field(default_factory=lambda: ["user"])


class BootstrapAdminResponse(BaseModel):
    user: UserProfile
    created: bool = False


class TokenIssueRequest(BaseModel):
    user_id: str = Field(min_length=1)
    email: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["user"])


class TokenClaims(BaseModel):
    sub: str
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    iat: int
    exp: int
    iss: str


class TokenIssueResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_at: datetime
    claims: TokenClaims


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenVerificationRequest(BaseModel):
    token: str = Field(min_length=1)


class TokenVerificationResponse(BaseModel):
    valid: bool
    claims: TokenClaims
