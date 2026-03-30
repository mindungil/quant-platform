from collections import defaultdict

from app.models.signal import SignalEvaluationResponse
from app.core.config import settings
from shared.persistence import SqlStore, deserialize_json, serialize_json


class SignalRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[SignalEvaluationResponse]] = defaultdict(list)
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

    def save(self, asset: str, evaluation: SignalEvaluationResponse) -> None:
        self._items[asset].append(evaluation)
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

    def get_latest(self, asset: str) -> SignalEvaluationResponse | None:
        history = self._items.get(asset, [])
        if history:
            return history[-1]
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
        if row is None:
            return None
        payload = deserialize_json(row["payload"])
        return SignalEvaluationResponse.model_validate(payload) if payload is not None else None

    def list_latest(self) -> list[SignalEvaluationResponse]:
        return [history[-1] for history in self._items.values() if history]

    def get_history(self, asset: str) -> list[SignalEvaluationResponse]:
        rows = self._store.fetch_all(
            """
            SELECT payload::text AS payload
            FROM signal_history
            WHERE asset = :asset
            ORDER BY timestamp ASC
            """,
            {"asset": asset},
        )
        if rows:
            return [SignalEvaluationResponse.model_validate(deserialize_json(row["payload"])) for row in rows]
        return self._items.get(asset, [])


signal_repository = SignalRepository()
