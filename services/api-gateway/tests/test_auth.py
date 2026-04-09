import jwt
import pytest
from fastapi import HTTPException

from app.core.auth import build_internal_admin_headers, require_role
from app.core.config import settings
from app.models.auth import GatewayPrincipal


def test_require_role_allows_admin_token() -> None:
    token = jwt.encode(
        {
            "sub": "user-1",
            "email": "admin@example.com",
            "roles": ["user", "admin"],
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        headers={"typ": "JWT"},
    )

    principal = require_role("admin")(authorization=f"Bearer {token}")

    assert principal.user_id == "user-1"
    assert "admin" in principal.roles


def test_require_role_rejects_non_admin_token() -> None:
    token = jwt.encode(
        {
            "sub": "user-2",
            "email": "user@example.com",
            "roles": ["user"],
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        headers={"typ": "JWT"},
    )

    with pytest.raises(HTTPException) as exc:
        require_role("admin")(authorization=f"Bearer {token}")

    assert exc.value.status_code == 403


def test_build_internal_admin_headers_shapes_signature() -> None:
    principal = GatewayPrincipal(
        user_id="user-1",
        email="admin@example.com",
        roles=["user", "admin"],
        forwarded_headers={"X-User-ID": "user-1"},
    )

    headers = build_internal_admin_headers(principal, "/admin/users")

    assert headers["X-User-ID"] == "user-1"
    assert headers["X-Internal-Actor-User-ID"] == "user-1"
    assert headers["X-Internal-Admin-Signature"]
