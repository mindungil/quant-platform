from uuid import uuid4

from app.core.tokens import issue_access_token, issue_for_profile, refresh_access_token, verify_access_token
from app.db.repository import AuthRepository, auth_repository
from app.models.auth import RefreshTokenRequest, TokenIssueRequest, UserRegistrationRequest


def test_issue_and_verify_roundtrip() -> None:
    response = issue_access_token(
        TokenIssueRequest(user_id="user-123", email="user@example.com", roles=["user", "admin"])
    )

    verified = verify_access_token(response.access_token)

    assert verified.valid is True
    assert verified.claims.sub == "user-123"
    assert "admin" in verified.claims.roles


def test_refresh_token_issues_new_access_token(monkeypatch) -> None:
    repo = AuthRepository()
    monkeypatch.setattr("app.db.repository.auth_repository", repo)
    monkeypatch.setattr("app.core.tokens.auth_repository", repo)
    profile = repo.register(
        UserRegistrationRequest(
            email=f"refresh-{uuid4().hex}@example.com",
            password="Password123!",
            display_name="Refresh User",
            plan="premium",
        )
    )
    issued = issue_for_profile(profile)
    refreshed = refresh_access_token(RefreshTokenRequest(refresh_token=issued.refresh_token))

    assert refreshed is not None
    assert refreshed.claims.sub == profile.user_id
