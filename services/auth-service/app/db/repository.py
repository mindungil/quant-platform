from __future__ import annotations

import hashlib
from uuid import uuid4

from app.models.auth import UserProfile, UserRegistrationRequest


class AuthRepository:
    def __init__(self) -> None:
        self._users_by_email: dict[str, dict] = {}
        self._refresh_tokens: dict[str, str] = {}

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def register(self, payload: UserRegistrationRequest) -> UserProfile:
        user_id = str(uuid4())
        record = {
            "user_id": user_id,
            "email": payload.email.lower(),
            "display_name": payload.display_name,
            "plan": payload.plan.upper(),
            "roles": ["user"],
            "automation_enabled": payload.plan.lower() == "premium",
            "password_hash": self._hash_password(payload.password),
        }
        self._users_by_email[record["email"]] = record
        return self._profile(record)

    def login(self, email: str, password: str) -> UserProfile | None:
        record = self._users_by_email.get(email.lower())
        if record is None:
            return None
        if record["password_hash"] != self._hash_password(password):
            return None
        return self._profile(record)

    def get_by_user_id(self, user_id: str) -> UserProfile | None:
        for record in self._users_by_email.values():
            if record["user_id"] == user_id:
                return self._profile(record)
        return None

    def store_refresh_token(self, user_id: str, refresh_token: str) -> None:
        self._refresh_tokens[refresh_token] = user_id

    def consume_refresh_token(self, refresh_token: str) -> UserProfile | None:
        user_id = self._refresh_tokens.get(refresh_token)
        if user_id is None:
            return None
        return self.get_by_user_id(user_id)

    def _profile(self, record: dict) -> UserProfile:
        return UserProfile(
            user_id=record["user_id"],
            email=record["email"],
            display_name=record["display_name"],
            plan=record["plan"],
            roles=record["roles"],
            automation_enabled=record["automation_enabled"],
        )


auth_repository = AuthRepository()
