from collections import defaultdict

from app.models.feature import CandlePayload, FeatureResponse
from app.core.config import settings
from shared.persistence import RedisStore, SqlStore, deserialize_json, serialize_json


class CandleRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[CandlePayload]] = defaultdict(list)

    def add(self, asset: str, candle: CandlePayload) -> None:
        self._items[asset].append(candle)
        self._items[asset] = sorted(self._items[asset], key=lambda item: item.timestamp)[-500:]

    def get(self, asset: str) -> list[CandlePayload]:
        return self._items[asset]

    def list_assets(self) -> list[str]:
        return sorted(self._items.keys())


class FeatureRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[FeatureResponse]] = defaultdict(list)
        self._store = SqlStore(settings.timescale_url)
        self._cache = RedisStore(settings.redis_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_history (
                asset TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL,
                PRIMARY KEY (asset, timestamp)
            )
            """
        )

    def save(self, asset: str, feature: FeatureResponse) -> None:
        self._items[asset].append(feature)
        self._store.execute(
            """
            INSERT INTO feature_history (asset, timestamp, payload)
            VALUES (:asset, :timestamp, CAST(:payload AS JSONB))
            ON CONFLICT (asset, timestamp) DO UPDATE SET payload = EXCLUDED.payload
            """,
            {"asset": asset, "timestamp": feature.timestamp, "payload": serialize_json(feature.model_dump(mode="json"))},
        )
        self._cache.hset_json("feature-latest", asset, feature.model_dump(mode="json"))

    def get_latest(self, asset: str) -> FeatureResponse | None:
        cached = self._cache.hget_json("feature-latest", asset)
        if cached is not None:
            return FeatureResponse.model_validate(cached)
        history = self._items.get(asset, [])
        if history:
            return history[-1]
        row = self._store.fetch_one(
            """
            SELECT payload::text AS payload
            FROM feature_history
            WHERE asset = :asset
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"asset": asset},
        )
        if row is None:
            return None
        payload = deserialize_json(row["payload"])
        return FeatureResponse.model_validate(payload) if payload is not None else None

    def get_history(self, asset: str) -> list[FeatureResponse]:
        rows = self._store.fetch_all(
            """
            SELECT payload::text AS payload
            FROM feature_history
            WHERE asset = :asset
            ORDER BY timestamp ASC
            """,
            {"asset": asset},
        )
        if rows:
            return [FeatureResponse.model_validate(deserialize_json(row["payload"])) for row in rows]
        return self._items.get(asset, [])


candle_repository = CandleRepository()
feature_repository = FeatureRepository()
