from collections import defaultdict

from app.core.config import settings
from app.models.agent import DecisionRecord
from shared.persistence import SqlStore, deserialize_json, serialize_json


class DecisionRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[DecisionRecord]] = defaultdict(list)
        self._store = SqlStore(settings.postgres_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS crypto_decisions (
                decision_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                user_id TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def save(self, asset: str, record: DecisionRecord) -> None:
        self._items[asset].append(record)
        self._store.execute(
            """
            INSERT INTO crypto_decisions (decision_id, asset, user_id, payload, created_at)
            VALUES (:decision_id, :asset, :user_id, CAST(:payload AS JSONB), :created_at)
            ON CONFLICT (decision_id) DO UPDATE SET
                asset = EXCLUDED.asset,
                user_id = EXCLUDED.user_id,
                payload = EXCLUDED.payload,
                created_at = EXCLUDED.created_at
            """,
            {
                "decision_id": record.decision_id,
                "asset": asset,
                "user_id": record.user_id,
                "payload": serialize_json(record.model_dump(mode="json")),
                "created_at": record.timestamp,
            },
        )

    def get_latest(self, asset: str) -> DecisionRecord | None:
        row = self._store.fetch_one(
            """
            SELECT payload
            FROM crypto_decisions
            WHERE asset = :asset
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"asset": asset},
        )
        if row is not None:
            try:
                return DecisionRecord.model_validate(deserialize_json(row["payload"]) or {})
            except Exception:
                pass
        history = self._items.get(asset, [])
        return history[-1] if history else None

    def get_history(self, asset: str) -> list[DecisionRecord]:
        rows = self._store.fetch_all(
            """
            SELECT payload
            FROM crypto_decisions
            WHERE asset = :asset
            ORDER BY created_at ASC
            """,
            {"asset": asset},
        )
        if rows:
            # Skip malformed/legacy payloads (e.g. rows written before the
            # DecisionRecord schema change) rather than 500ing the endpoint.
            result: list[DecisionRecord] = []
            for row in rows:
                try:
                    result.append(
                        DecisionRecord.model_validate(deserialize_json(row["payload"]) or {})
                    )
                except Exception:
                    continue
            return result
        return self._items.get(asset, [])


    def get_by_correlation_id(self, correlation_id: str) -> dict | None:
        row = self._store.fetch_one(
            """
            SELECT payload
            FROM crypto_decisions
            WHERE payload->>'correlation_id' = :cid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {"cid": correlation_id},
        )
        if row is not None:
            return deserialize_json(row["payload"]) or {}
        return None


decision_repository = DecisionRepository()
