from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import settings
from shared.persistence import SqlStore, deserialize_json, serialize_json


class OrchestratorRepository:
    def __init__(self) -> None:
        self._store = SqlStore(settings.postgres_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS orchestrator_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def save(self, payload: dict) -> dict:
        snapshot_id = str(uuid4())
        created_at = datetime.now(UTC)
        self._store.execute(
            """
            INSERT INTO orchestrator_snapshots (snapshot_id, payload, created_at)
            VALUES (:snapshot_id, CAST(:payload AS JSONB), :created_at)
            """,
            {"snapshot_id": snapshot_id, "payload": serialize_json(payload), "created_at": created_at},
        )
        return {"snapshot_id": snapshot_id, "payload": payload, "created_at": created_at.isoformat()}

    def latest(self) -> dict | None:
        row = self._store.fetch_one(
            """
            SELECT snapshot_id, payload, created_at
            FROM orchestrator_snapshots
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        if row is None:
            return None
        return {
            "snapshot_id": row["snapshot_id"],
            "payload": deserialize_json(row["payload"]) or {},
            "created_at": row["created_at"],
        }


orchestrator_repository = OrchestratorRepository()
