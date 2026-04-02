import os

from app.core.crypto import decrypt, encrypt
from app.models.credential import CredentialCreate, CredentialMaskedResponse, CredentialResponse
from shared.persistence import SqlStore


class CredentialRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, str | bool | None]] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS credential_records (
                user_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                api_key_encrypted TEXT NOT NULL,
                api_secret_encrypted TEXT NOT NULL,
                label TEXT,
                sandbox BOOLEAN NOT NULL DEFAULT TRUE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, exchange)
            )
            """
        )

    def _mask(self, value: str) -> str:
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}***{value[-2:]}"

    def save(self, payload: CredentialCreate) -> CredentialMaskedResponse:
        encrypted = {
            "api_key": encrypt(payload.api_key),
            "api_secret": encrypt(payload.api_secret),
            "label": payload.label,
            "sandbox": payload.sandbox,
        }
        self._items[(payload.user_id, payload.exchange)] = encrypted
        self._store.execute(
            """
            INSERT INTO credential_records (
                user_id, exchange, api_key_encrypted, api_secret_encrypted, label, sandbox, updated_at
            ) VALUES (
                :user_id, :exchange, :api_key_encrypted, :api_secret_encrypted, :label, :sandbox, NOW()
            )
            ON CONFLICT (user_id, exchange) DO UPDATE SET
                api_key_encrypted = EXCLUDED.api_key_encrypted,
                api_secret_encrypted = EXCLUDED.api_secret_encrypted,
                label = EXCLUDED.label,
                sandbox = EXCLUDED.sandbox,
                updated_at = NOW()
            """,
            {
                "user_id": payload.user_id,
                "exchange": payload.exchange,
                "api_key_encrypted": encrypted["api_key"],
                "api_secret_encrypted": encrypted["api_secret"],
                "label": payload.label,
                "sandbox": payload.sandbox,
            },
        )
        return self.get_masked(payload.user_id, payload.exchange)

    def get(self, user_id: str, exchange: str) -> CredentialResponse | None:
        value = self._items.get((user_id, exchange))
        if value is None:
            row = self._store.fetch_one(
                """
                SELECT api_key_encrypted, api_secret_encrypted, label, sandbox
                FROM credential_records
                WHERE user_id = :user_id AND exchange = :exchange
                """,
                {"user_id": user_id, "exchange": exchange},
            )
            if row is not None:
                value = {
                    "api_key": row["api_key_encrypted"],
                    "api_secret": row["api_secret_encrypted"],
                    "label": row["label"],
                    "sandbox": bool(row["sandbox"]),
                }
                self._items[(user_id, exchange)] = value
        if value is None:
            return None
        return CredentialResponse(
            user_id=user_id,
            exchange=exchange,
            label=value["label"],
            sandbox=bool(value["sandbox"]),
            api_key=decrypt(str(value["api_key"])),
            api_secret=decrypt(str(value["api_secret"])),
        )

    def list_for_user(self, user_id: str) -> list[CredentialMaskedResponse]:
        rows = self._store.fetch_all(
            "SELECT exchange FROM credential_records WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        results = []
        for row in rows:
            masked = self.get_masked(user_id, row["exchange"])
            if masked:
                results.append(masked)
        return results

    def delete(self, user_id: str, exchange: str) -> bool:
        self._items.pop((user_id, exchange), None)
        self._store.execute(
            "DELETE FROM credential_records WHERE user_id = :user_id AND exchange = :exchange",
            {"user_id": user_id, "exchange": exchange},
        )
        return True

    def get_masked(self, user_id: str, exchange: str) -> CredentialMaskedResponse | None:
        credential = self.get(user_id, exchange)
        if credential is None:
            return None
        return CredentialMaskedResponse(
            user_id=credential.user_id,
            exchange=credential.exchange,
            label=credential.label,
            sandbox=credential.sandbox,
            api_key_masked=self._mask(credential.api_key),
            api_secret_masked=self._mask(credential.api_secret),
        )


credential_repository = CredentialRepository()
