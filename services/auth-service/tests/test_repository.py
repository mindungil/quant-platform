from uuid import uuid4

from app.db.repository import AuthRepository
from app.models.auth import UserRegistrationRequest


def test_register_rejects_duplicate_email() -> None:
    repo = AuthRepository()
    email = f"duplicate-{uuid4().hex}@example.com"
    payload = UserRegistrationRequest(
        email=email,
        password="password123",
        display_name="Duplicate",
        plan="free",
    )
    repo.register(payload)

    try:
        repo.register(payload)
    except ValueError as exc:
        assert str(exc) == "user_exists"
    else:
        raise AssertionError("expected duplicate registration to fail")


def test_update_roles_preserves_user_role() -> None:
    repo = AuthRepository()
    profile = repo.register(
        UserRegistrationRequest(
            email=f"adminize-{uuid4().hex}@example.com",
            password="password123",
            display_name="Adminize",
            plan="premium",
        )
    )

    updated = repo.update_roles(profile.user_id, ["admin"])

    assert updated is not None
    assert updated.roles == ["user", "admin"]
