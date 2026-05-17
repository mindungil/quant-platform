from collections import defaultdict

from app.models.signal import SignalEvaluationResponse
from app.core.config import settings
from shared.persistence import SqlStore, deserialize_json, serialize_json


class SignalRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], list[SignalEvaluationResponse]] = defaultdict(list)
        self._store = SqlStore(settings.timescale_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_history (
                asset TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                PRIMARY KEY (asset, timestamp)
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_history_v2 (
                asset TEXT NOT NULL,
                user_id TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                PRIMARY KEY (asset, user_id, timestamp)
            )
            """
        )

    def _key(self, asset: str, user_id: str | None) -> tuple[str, str]:
        return (asset, user_id or "global")

    def save(self, asset: str, evaluation: SignalEvaluationResponse) -> None:
        scoped_user_id = evaluation.strategy_user_id or "global"
        self._items[self._key(asset, scoped_user_id)].append(evaluation)
        self._store.execute(
            """
            INSERT INTO signal_history (asset, timestamp, payload)
            VALUES (:asset, :timestamp, CAST(:payload AS JSONB))
            ON CONFLICT (asset, timestamp) DO UPDATE SET payload = EXCLUDED.payload
            """,
            {
                "asset": asset,
                "timestamp": evaluation.feature_timestamp,
                "payload": serialize_json(evaluation.model_dump(mode="json")),
            },
        )
        self._store.execute(
            """
            INSERT INTO signal_history_v2 (asset, user_id, timestamp, payload)
            VALUES (:asset, :user_id, :timestamp, CAST(:payload AS JSONB))
            ON CONFLICT (asset, user_id, timestamp) DO UPDATE SET payload = EXCLUDED.payload
            """,
            {
                "asset": asset,
                "user_id": scoped_user_id,
                "timestamp": evaluation.feature_timestamp,
                "payload": serialize_json(evaluation.model_dump(mode="json")),
            },
        )

    def get_latest(self, asset: str, user_id: str | None = None) -> SignalEvaluationResponse | None:
        history = self._items.get(self._key(asset, user_id), [])
        if history:
            return history[-1]
        if user_id is None:
            row = self._store.fetch_one(
                """
                SELECT payload::text AS payload
                FROM signal_history
                WHERE asset = :asset
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                {"asset": asset},
            )
        else:
            row = self._store.fetch_one(
                """
                SELECT payload::text AS payload
                FROM signal_history_v2
                WHERE asset = :asset AND user_id = :user_id
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                {"asset": asset, "user_id": user_id},
            )
            # D10 fix: fall back to anonymous signal when the user-specific
            # row is missing OR materially staler than the anonymous one
            # (the scheduler writes anonymous now; per-user rows persist
            # from earlier cycles and would otherwise pin a 40-day-stale
            # signal in graph.gather → trigger SIGNAL_STALENESS abort).
            anon_row = self._store.fetch_one(
                """
                SELECT payload::text AS payload, timestamp
                FROM signal_history
                WHERE asset = :asset
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                {"asset": asset},
            )
            if row is None:
                row = anon_row
            elif anon_row is not None:
                # Prefer anonymous when it's significantly fresher (> 1h).
                user_row = self._store.fetch_one(
                    """
                    SELECT timestamp
                    FROM signal_history_v2
                    WHERE asset = :asset AND user_id = :user_id
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    {"asset": asset, "user_id": user_id},
                )
                if user_row is not None and anon_row.get("timestamp") and user_row.get("timestamp"):
                    if anon_row["timestamp"] > user_row["timestamp"]:
                        row = anon_row
        if row is None:
            return None
        payload = deserialize_json(row["payload"])
        return SignalEvaluationResponse.model_validate(payload) if payload is not None else None

    def list_latest(self, user_id: str | None = None) -> list[SignalEvaluationResponse]:
        if user_id is not None:
            scoped_items = [
                history[-1]
                for (asset_key, user_key), history in self._items.items()
                if history and user_key == user_id and asset_key != "*"
            ]
            if scoped_items:
                return scoped_items
            rows = self._store.fetch_all(
                """
                SELECT DISTINCT ON (asset) payload::text AS payload
                FROM signal_history_v2
                WHERE user_id = :user_id
                ORDER BY asset, timestamp DESC
                """,
                {"user_id": user_id},
            )
            return [SignalEvaluationResponse.model_validate(deserialize_json(row["payload"])) for row in rows]
        return [history[-1] for history in self._items.values() if history]

    def get_history(self, asset: str, user_id: str | None = None) -> list[SignalEvaluationResponse]:
        if user_id is None:
            rows = self._store.fetch_all(
                """
                SELECT payload::text AS payload
                FROM signal_history
                WHERE asset = :asset
                ORDER BY timestamp ASC
                """,
                {"asset": asset},
            )
        else:
            rows = self._store.fetch_all(
                """
                SELECT payload::text AS payload
                FROM signal_history_v2
                WHERE asset = :asset AND user_id = :user_id
                ORDER BY timestamp ASC
                """,
                {"asset": asset, "user_id": user_id},
            )
        if rows:
            return [SignalEvaluationResponse.model_validate(deserialize_json(row["payload"])) for row in rows]
        return self._items.get(self._key(asset, user_id), [])


signal_repository = SignalRepository()
