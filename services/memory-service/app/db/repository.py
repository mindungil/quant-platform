from app.models.memory import MemoryRecord
import os
from shared.persistence import SqlStore, deserialize_json, serialize_json


class MemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, MemoryRecord] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                memory_type TEXT NOT NULL,
                asset TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                signal_score DOUBLE PRECISION NOT NULL,
                action TEXT NOT NULL,
                strategy_id TEXT,
                reasoning TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
                formula_name TEXT,
                regime_label TEXT,
                trade_outcome DOUBLE PRECISION,
                outcome_sharpe DOUBLE PRECISION,
                links JSONB NOT NULL DEFAULT '[]'::jsonb,
                link_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_reinforced_at TIMESTAMPTZ
            )
            """
        )

    def save(self, record: MemoryRecord) -> None:
        self._items[record.id] = record
        self._store.execute(
            """
            INSERT INTO memory_records (
                id, user_id, timestamp, memory_type, asset, asset_type, signal_score, action, strategy_id,
                reasoning, formula_name, regime_label, trade_outcome, outcome_sharpe,
                metadata, embedding, links, link_weights, last_reinforced_at
            ) VALUES (
                :id, :user_id, :timestamp, :memory_type, :asset, :asset_type, :signal_score, :action, :strategy_id,
                :reasoning, :formula_name, :regime_label, :trade_outcome, :outcome_sharpe,
                CAST(:metadata AS JSONB), CAST(:embedding AS JSONB), CAST(:links AS JSONB), CAST(:link_weights AS JSONB), :last_reinforced_at
            )
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                timestamp = EXCLUDED.timestamp,
                memory_type = EXCLUDED.memory_type,
                asset = EXCLUDED.asset,
                asset_type = EXCLUDED.asset_type,
                signal_score = EXCLUDED.signal_score,
                action = EXCLUDED.action,
                strategy_id = EXCLUDED.strategy_id,
                reasoning = EXCLUDED.reasoning,
                formula_name = EXCLUDED.formula_name,
                regime_label = EXCLUDED.regime_label,
                trade_outcome = EXCLUDED.trade_outcome,
                outcome_sharpe = EXCLUDED.outcome_sharpe,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                links = EXCLUDED.links,
                link_weights = EXCLUDED.link_weights,
                last_reinforced_at = EXCLUDED.last_reinforced_at
            """,
            {
                **record.model_dump(mode="json"),
                "metadata": serialize_json(record.metadata),
                "embedding": serialize_json(record.embedding),
                "links": serialize_json(record.links),
                "link_weights": serialize_json(record.link_weights),
            },
        )

    def get(self, memory_id: str) -> MemoryRecord | None:
        item = self._items.get(memory_id)
        if item is not None:
            return item
        row = self._store.fetch_one("SELECT * FROM memory_records WHERE id = :memory_id", {"memory_id": memory_id})
        if row is None:
            return None
        return self._hydrate(row)

    def list_all(self, user_id: str | None = None) -> list[MemoryRecord]:
        if user_id is None:
            rows = self._store.fetch_all(
                """
                SELECT * FROM memory_records
                ORDER BY timestamp DESC
                """
            )
        else:
            rows = self._store.fetch_all(
                """
                SELECT * FROM memory_records
                WHERE user_id = :user_id
                ORDER BY timestamp DESC
                """,
                {"user_id": user_id},
            )
        if rows:
            return [self._hydrate(row) for row in rows]
        items = list(self._items.values())
        if user_id is None:
            return items
        return [item for item in items if item.user_id == user_id]

    def reinforce(self, memory_id: str, trade_outcome: float, outcome_sharpe: float) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        self._store.execute(
            """
            UPDATE memory_records
            SET trade_outcome = :trade_outcome,
                outcome_sharpe = :outcome_sharpe,
                last_reinforced_at = :now
            WHERE id = :memory_id
            """,
            {
                "memory_id": memory_id,
                "trade_outcome": trade_outcome,
                "outcome_sharpe": outcome_sharpe,
                "now": now,
            },
        )
        # Update in-memory cache if present
        if memory_id in self._items:
            self._items[memory_id].trade_outcome = trade_outcome
            self._items[memory_id].outcome_sharpe = outcome_sharpe
            self._items[memory_id].last_reinforced_at = now

    def _hydrate(self, row: dict) -> MemoryRecord:
        payload = dict(row)
        payload["metadata"] = deserialize_json(row["metadata"]) or {}
        payload["embedding"] = deserialize_json(row["embedding"]) or []
        payload["links"] = deserialize_json(row["links"]) or []
        payload["link_weights"] = deserialize_json(row["link_weights"]) or {}
        payload["formula_name"] = row.get("formula_name")
        payload["regime_label"] = row.get("regime_label")
        payload["trade_outcome"] = row.get("trade_outcome")
        payload["outcome_sharpe"] = row.get("outcome_sharpe")
        return MemoryRecord(**payload)


memory_repository = MemoryRepository()
