from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4

import bcrypt

from app.core.config import settings
from app.models.auth import UserProfile, UserRegistrationRequest
from shared.persistence import SqlStore, deserialize_json, serialize_json


class AuthRepository:
    def __init__(self) -> None:
        self._users_by_email: dict[str, dict] = {}
        self._users_by_id: dict[str, dict] = {}
        self._refresh_tokens: dict[str, str] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", settings.postgres_url))
        self._ensure_schema()

    def _hash_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def _verify_password(self, password: str, password_hash: str) -> bool:
        # Support legacy SHA256 hashes (64 hex chars) with auto-upgrade
        if len(password_hash) == 64 and not password_hash.startswith("$2"):
            return hashlib.sha256(password.encode("utf-8")).hexdigest() == password_hash
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                plan TEXT NOT NULL,
                roles JSONB NOT NULL DEFAULT '["user"]'::jsonb,
                automation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
                refresh_token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def _normalize_roles(self, roles: list[str]) -> list[str]:
        normalized = {role.strip().lower() for role in roles if role.strip()}
        normalized.discard("free")
        normalized.discard("pro")
        normalized.discard("premium")
        normalized.add("user")
        ordered = ["user"]
        if "admin" in normalized:
            ordered.append("admin")
        return ordered

    def _record_from_row(self, row: dict) -> dict:
        return {
            "user_id": row["user_id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "plan": row["plan"],
            "roles": self._normalize_roles(deserialize_json(row["roles"]) or ["user"]),
            "automation_enabled": bool(row["automation_enabled"]),
            "password_hash": row["password_hash"],
            "created_at": row.get("created_at"),
        }

    def _persist_user(self, record: dict) -> None:
        self._store.execute(
            """
            INSERT INTO auth_users (
                user_id, email, display_name, plan, roles, automation_enabled, password_hash, created_at
            ) VALUES (
                :user_id, :email, :display_name, :plan, CAST(:roles AS JSONB), :automation_enabled, :password_hash, :created_at
            )
            ON CONFLICT (user_id) DO UPDATE SET
                email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                plan = EXCLUDED.plan,
                roles = EXCLUDED.roles,
                automation_enabled = EXCLUDED.automation_enabled,
                password_hash = EXCLUDED.password_hash
            """,
            {
                **record,
                "roles": serialize_json(self._normalize_roles(record["roles"])),
                "created_at": record.get("created_at") or datetime.now(UTC),
            },
        )

    def _persist_refresh_token(self, user_id: str, refresh_token: str) -> None:
        self._store.execute(
            """
            INSERT INTO auth_refresh_tokens (refresh_token, user_id)
            VALUES (:refresh_token, :user_id)
            ON CONFLICT (refresh_token) DO UPDATE SET
                user_id = EXCLUDED.user_id
            """,
            {"refresh_token": refresh_token, "user_id": user_id},
        )

    def _get_record_by_email(self, email: str) -> dict | None:
        cached = self._users_by_email.get(email.lower())
        if cached is not None:
            return cached
        row = self._store.fetch_one("SELECT * FROM auth_users WHERE email = :email", {"email": email.lower()})
        if row is None:
            return None
        record = self._record_from_row(row)
        self._users_by_email[record["email"]] = record
        self._users_by_id[record["user_id"]] = record
        return record

    def _get_record_by_user_id(self, user_id: str) -> dict | None:
        cached = self._users_by_id.get(user_id)
        if cached is not None:
            return cached
        row = self._store.fetch_one("SELECT * FROM auth_users WHERE user_id = :user_id", {"user_id": user_id})
        if row is None:
            return None
        record = self._record_from_row(row)
        self._users_by_email[record["email"]] = record
        self._users_by_id[record["user_id"]] = record
        return record

    def register(self, payload: UserRegistrationRequest) -> UserProfile:
        if self._get_record_by_email(payload.email) is not None:
            raise ValueError("user_exists")
        user_id = str(uuid4())
        record = {
            "user_id": user_id,
            "email": payload.email.lower(),
            "display_name": payload.display_name,
            "plan": payload.plan.upper(),
            "roles": self._normalize_roles(["user"]),
            "automation_enabled": payload.plan.lower() == "premium",
            "password_hash": self._hash_password(payload.password),
            "created_at": datetime.now(UTC),
        }
        self._users_by_email[record["email"]] = record
        self._users_by_id[record["user_id"]] = record
        self._persist_user(record)
        return self._profile(record)

    def login(self, email: str, password: str) -> UserProfile | None:
        record = self._get_record_by_email(email)
        if record is None:
            return None
        if not self._verify_password(password, record["password_hash"]):
            return None
        # Auto-upgrade legacy SHA256 hashes to bcrypt
        if len(record["password_hash"]) == 64 and not record["password_hash"].startswith("$2"):
            record["password_hash"] = self._hash_password(password)
            self._persist_user(record)
        return self._profile(record)

    def get_by_user_id(self, user_id: str) -> UserProfile | None:
        record = self._get_record_by_user_id(user_id)
        return None if record is None else self._profile(record)

    def list_users(self) -> list[UserProfile]:
        rows = self._store.fetch_all("SELECT * FROM auth_users ORDER BY created_at ASC, email ASC")
        if rows:
            return [self._profile(self._record_from_row(row)) for row in rows]
        return [self._profile(record) for record in sorted(self._users_by_email.values(), key=lambda item: item["email"])]

    def update_roles(self, user_id: str, roles: list[str]) -> UserProfile | None:
        record = self._get_record_by_user_id(user_id)
        if record is None:
            return None
        record["roles"] = self._normalize_roles(roles)
        self._users_by_email[record["email"]] = record
        self._users_by_id[record["user_id"]] = record
        self._persist_user(record)
        return self._profile(record)

    def bootstrap_admin(self) -> tuple[UserProfile, bool] | None:
        if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
            return None
        existing = self._get_record_by_email(settings.bootstrap_admin_email)
        created = existing is None
        if existing is None:
            payload = UserRegistrationRequest(
                email=settings.bootstrap_admin_email,
                password=settings.bootstrap_admin_password,
                display_name=settings.bootstrap_admin_display_name,
                plan="premium",
            )
            profile = self.register(payload)
            existing = self._get_record_by_user_id(profile.user_id)
            assert existing is not None

        existing["display_name"] = settings.bootstrap_admin_display_name or existing["display_name"]
        existing["password_hash"] = self._hash_password(settings.bootstrap_admin_password)
        existing["roles"] = self._normalize_roles(list(existing.get("roles", ["user"])) + ["admin"])
        if not existing.get("plan"):
            existing["plan"] = "PREMIUM"
        existing["automation_enabled"] = str(existing["plan"]).lower() == "premium"
        self._users_by_email[existing["email"]] = existing
        self._users_by_id[existing["user_id"]] = existing
        self._persist_user(existing)
        return self._profile(existing), created

    def store_refresh_token(self, user_id: str, refresh_token: str) -> None:
        self._refresh_tokens[refresh_token] = user_id
        self._persist_refresh_token(user_id, refresh_token)

    def consume_refresh_token(self, refresh_token: str) -> UserProfile | None:
        user_id = self._refresh_tokens.get(refresh_token)
        if user_id is None:
            row = self._store.fetch_one(
                "SELECT user_id FROM auth_refresh_tokens WHERE refresh_token = :refresh_token",
                {"refresh_token": refresh_token},
            )
            user_id = None if row is None else row["user_id"]
        if user_id is None:
            return None
        return self.get_by_user_id(user_id)

    def _profile(self, record: dict) -> UserProfile:
        return UserProfile(
            user_id=record["user_id"],
            email=record["email"],
            display_name=record["display_name"],
            plan=record["plan"],
            roles=self._normalize_roles(record["roles"]),
            automation_enabled=record["automation_enabled"],
        )


auth_repository = AuthRepository()
