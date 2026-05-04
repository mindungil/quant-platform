from __future__ import annotations

import os
from datetime import datetime, timezone

from app.models.external_data import ExternalContextSnapshot
from shared.persistence import SqlStore, deserialize_json, serialize_json

UTC = timezone.utc


class ExternalSnapshotRepository:
    def __init__(self) -> None:
        self._store = SqlStore(
            os.getenv(
                "POSTGRES_URL_MARKET",
                os.getenv("TIMESCALE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/market"),
            )
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS external_context_snapshots (
                asset TEXT PRIMARY KEY,
                snapshot JSONB NOT NULL,
                source TEXT NOT NULL DEFAULT 'live',
                degraded_mode BOOLEAN NOT NULL DEFAULT FALSE,
                stale BOOLEAN NOT NULL DEFAULT FALSE,
                source_timestamp TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._store.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_external_context_snapshots_created_at
            ON external_context_snapshots (created_at DESC)
            """
        )

    def upsert(self, snapshot: ExternalContextSnapshot) -> ExternalContextSnapshot:
        self._store.execute(
            """
            INSERT INTO external_context_snapshots (
                asset, snapshot, source, degraded_mode, stale, source_timestamp, created_at
            ) VALUES (
                :asset, CAST(:snapshot AS JSONB), :source, :degraded_mode, :stale, :source_timestamp, :created_at
            )
            ON CONFLICT (asset) DO UPDATE SET
                snapshot = EXCLUDED.snapshot,
                source = EXCLUDED.source,
                degraded_mode = EXCLUDED.degraded_mode,
                stale = EXCLUDED.stale,
                source_timestamp = EXCLUDED.source_timestamp,
                created_at = EXCLUDED.created_at
            """,
            {
                "asset": snapshot.asset,
                "snapshot": serialize_json(snapshot.model_dump(mode="json")),
                "source": snapshot.source,
                "degraded_mode": snapshot.degraded_mode,
                "stale": snapshot.stale,
                "source_timestamp": snapshot.source_timestamp,
                "created_at": datetime.now(UTC),
            },
        )
        return snapshot

    def get_latest(self, asset: str) -> ExternalContextSnapshot | None:
        row = self._store.fetch_one(
            """
            SELECT snapshot
            FROM external_context_snapshots
            WHERE asset = :asset
            """,
            {"asset": asset},
        )
        if row is None:
            return None
        payload = deserialize_json(row["snapshot"]) or {}
        return ExternalContextSnapshot.model_validate(payload)


snapshot_repository = ExternalSnapshotRepository()
