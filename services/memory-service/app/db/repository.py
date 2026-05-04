from __future__ import annotations

import hashlib
import logging
import os

import numpy as np

from app.models.memory import MemoryRecord
from shared.persistence import SqlStore, deserialize_json, serialize_json

logger = logging.getLogger("memory-service")

# ---------------------------------------------------------------------------
# Semantic embedding support (sentence-transformers with hash-based fallback)
# ---------------------------------------------------------------------------
_embedder = None
_embedder_loaded = False


def _get_embedder():
    global _embedder, _embedder_loaded
    if not _embedder_loaded:
        _embedder_loaded = True
        try:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Loaded sentence-transformers model for semantic search")
        except Exception as e:
            logger.warning("sentence-transformers not available, using hash embeddings: %s", e)
    return _embedder


def _hash_embedding(key_parts: list[str]) -> list[float]:
    """Deterministic hash-based 384-dim embedding (fallback)."""
    seed = hashlib.sha256("|".join(key_parts).encode()).digest()
    rng = np.random.RandomState(int.from_bytes(seed[:4], "big"))
    vec = rng.randn(384).astype(np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()


def compute_embedding(text: str) -> list[float]:
    """Compute a 384-dim embedding using sentence-transformers or hash fallback."""
    model = _get_embedder()
    if model is not None:
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    else:
        return _hash_embedding([text])


class MemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, MemoryRecord] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # Enable pgvector extension for semantic search
        try:
            self._store.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            logger.warning("pgvector extension not available, semantic search disabled")

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

        # Add columns that may be missing from older table versions
        for col_def in [
            "formula_name TEXT",
            "regime_label TEXT",
            "trade_outcome DOUBLE PRECISION",
            "outcome_sharpe DOUBLE PRECISION",
        ]:
            col_name = col_def.split()[0]
            try:
                self._store.execute(
                    f"ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS {col_def}"
                )
            except Exception:
                pass  # column may already exist

        # Add pgvector column for semantic similarity search
        try:
            self._store.execute(
                "ALTER TABLE memory_records ADD COLUMN IF NOT EXISTS embedding_vec vector(384)"
            )
        except Exception:
            pass  # column may already exist or vector extension not available

    def _compute_embedding(self, record_data: dict) -> list[float]:
        """Compute a 384-dim embedding for similarity search.

        Uses sentence-transformers if available, otherwise falls back to
        a deterministic hash of key fields.
        """
        # Build a text representation of the record for semantic embedding
        text_parts = [
            str(record_data.get("asset", "")),
            str(record_data.get("action", "")),
            str(record_data.get("reasoning", "")),
            str(record_data.get("formula_name", "")),
            str(record_data.get("regime_label", "")),
        ]
        text = " ".join(p for p in text_parts if p)

        model = _get_embedder()
        if model is not None:
            embedding = model.encode(text, normalize_embeddings=True)
            return embedding.tolist()

        # Fallback: deterministic hash-based embedding
        key_parts = [
            str(record_data.get("asset", "")),
            str(record_data.get("action", "")),
            str(record_data.get("signal_score", 0)),
            str(record_data.get("formula_name", "")),
            str(record_data.get("regime_label", "")),
        ]
        return _hash_embedding(key_parts)

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
            scope_user_id=record.user_id,
        )

        # Compute and store pgvector embedding for semantic search
        embedding = self._compute_embedding(record.model_dump(mode="json"))
        try:
            self._store.execute(
                "UPDATE memory_records SET embedding_vec = :vec::vector WHERE id = :id",
                {"vec": str(embedding), "id": record.id},
                scope_user_id=record.user_id,
            )
        except Exception:
            pass  # vector extension may not be available

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
                scope_user_id=user_id,
            )
        if rows:
            return [self._hydrate(row) for row in rows]
        items = list(self._items.values())
        if user_id is None:
            return items
        return [item for item in items if item.user_id == user_id]

    def reinforce(self, memory_id: str, trade_outcome: float, outcome_sharpe: float) -> None:
        from datetime import datetime, timezone
        UTC = timezone.utc

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

    def search_similar(self, query_embedding: list[float], user_id: str | None = None, top_k: int = 10) -> list:
        """Search for similar memories using pgvector cosine similarity."""
        if user_id is None:
            return []
        where_clause = "WHERE user_id = :user_id"
        params: dict = {"vec": str(query_embedding), "top_k": top_k, "user_id": user_id}

        try:
            rows = self._store.fetch_all(
                f"""
                SELECT *, 1 - (embedding_vec <=> :vec::vector) as similarity
                FROM memory_records
                {where_clause}
                AND embedding_vec IS NOT NULL
                ORDER BY embedding_vec <=> :vec::vector
                LIMIT :top_k
                """,
                params,
            )
            return [self._hydrate(row) for row in rows]
        except Exception:
            return []

    def get_by_type(
        self,
        memory_type: str,
        user_id: str | None = None,
        asset: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        """Query memories by type (episode/knowledge/rule/state)."""
        where = "WHERE memory_type = :memory_type"
        params: dict = {"memory_type": memory_type, "limit": limit}
        if user_id:
            where += " AND user_id = :user_id"
            params["user_id"] = user_id
        if asset:
            where += " AND (asset = :asset OR asset = 'ALL')"
            params["asset"] = asset
        rows = self._store.fetch_all(
            f"SELECT * FROM memory_records {where} ORDER BY timestamp DESC LIMIT :limit",
            params,
        )
        return [self._hydrate(row) for row in rows] if rows else []

    def _hydrate(self, row: dict) -> MemoryRecord:
        payload = dict(row)
        # Remove pgvector column that is not part of the Pydantic model
        payload.pop("embedding_vec", None)
        payload.pop("similarity", None)
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
