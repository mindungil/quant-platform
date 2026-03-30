from datetime import datetime

from pydantic import BaseModel, Field


class UserRegistrationRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1)
    plan: str = "free"


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
