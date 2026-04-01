from __future__ import annotations

from app.core.config import settings
from app.models.candle import CandlePayload
from shared.persistence import SqlStore, serialize_json


class MarketDataRepository:
    def __init__(self) -> None:
        self._store = SqlStore(settings.timescale_url)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS market_candles (
                asset TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                open DOUBLE PRECISION NOT NULL,
                high DOUBLE PRECISION NOT NULL,
                low DOUBLE PRECISION NOT NULL,
                close DOUBLE PRECISION NOT NULL,
                volume DOUBLE PRECISION NOT NULL,
                anomaly_detected BOOLEAN NOT NULL DEFAULT FALSE,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (asset, timestamp)
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS market_anomalies (
                id BIGSERIAL PRIMARY KEY,
                asset TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                anomaly_detected BOOLEAN NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )

    def save(self, asset: str, candle: CandlePayload, *, anomaly_detected: bool) -> None:
        self._store.execute(
            """
            INSERT INTO market_candles (
                asset, timestamp, open, high, low, close, volume, anomaly_detected, metadata
            ) VALUES (
                :asset, :timestamp, :open, :high, :low, :close, :volume, :anomaly_detected, CAST(:metadata AS JSONB)
            )
            ON CONFLICT (asset, timestamp) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                anomaly_detected = EXCLUDED.anomaly_detected,
                metadata = EXCLUDED.metadata
            """,
            {
                "asset": asset,
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "anomaly_detected": anomaly_detected,
                "metadata": serialize_json({"source": "rest"}),
            },
        )
        if anomaly_detected:
            self._store.execute(
                """
                INSERT INTO market_anomalies (asset, timestamp, anomaly_detected, metadata)
                VALUES (:asset, :timestamp, :anomaly_detected, CAST(:metadata AS JSONB))
                """,
                {
                    "asset": asset,
                    "timestamp": candle.timestamp,
                    "anomaly_detected": anomaly_detected,
                    "metadata": serialize_json({"source": "rest"}),
                },
            )

    def get_history(self, asset: str, *, limit: int | None = None) -> list[CandlePayload]:
        if limit is not None:
            rows = self._store.fetch_all(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM market_candles
                WHERE asset = :asset
                ORDER BY timestamp DESC
                LIMIT :limit
                """,
                {"asset": asset, "limit": limit},
            )
            # Reverse to return in ascending order
            rows = list(reversed(rows))
        else:
            rows = self._store.fetch_all(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM market_candles
                WHERE asset = :asset
                ORDER BY timestamp ASC
                """,
                {"asset": asset},
            )
        return [CandlePayload(**row) for row in rows]

    def get_latest(self, asset: str) -> CandlePayload | None:
        row = self._store.fetch_one(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM market_candles
            WHERE asset = :asset
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            {"asset": asset},
        )
        if row is None:
            return None
        return CandlePayload(**row)


market_data_repository = MarketDataRepository()
