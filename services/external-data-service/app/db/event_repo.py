"""Event embeddings repository.

Manages the event_embeddings table: insert events, store embeddings,
search by vector similarity, and auto-label with price outcomes.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger("event-repo")


class EventRepository:
    """Repository for event_embeddings table (pgvector)."""

    def __init__(self) -> None:
        self._store = None

    def _get_store(self):
        if self._store is None:
            import os
            from shared.persistence import SqlStore
            url = os.getenv("TIMESCALE_URL") or os.getenv("POSTGRES_URL_MARKET")
            if not url:
                url = "postgresql+psycopg://postgres:postgres@localhost:5432/market"
            self._store = SqlStore(url=url)
        return self._store

    def insert_event(
        self,
        id: str,
        asset: str,
        timestamp: datetime,
        source: str,
        title: str,
        chunk_text: str,
        tier: str,
        nlp_score: float | None = None,
        nlp_confidence: float | None = None,
        body_preview: str | None = None,
        embedding: np.ndarray | None = None,
        price_at_event: float | None = None,
        volume_zscore: float | None = None,
        fng_value: int | None = None,
        volatility: float | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Insert or update an event in the store."""
        store = self._get_store()

        vec_str = None
        if embedding is not None:
            vec_str = "[" + ",".join(str(float(v)) for v in embedding.flatten()) + "]"

        try:
            store.execute(
                """
                INSERT INTO event_embeddings
                    (id, asset, timestamp, source, title, body_preview, chunk_text,
                     embedding, nlp_score, nlp_confidence, tier,
                     price_at_event, volume_zscore, fng_value, volatility, metadata)
                VALUES
                    (:id, :asset, :ts, :source, :title, :body_preview, :chunk_text,
                     CAST(:embedding AS vector), :nlp_score, :nlp_confidence, :tier,
                     :price_at_event, :volume_zscore, :fng_value, :volatility, CAST(:metadata AS jsonb))
                ON CONFLICT (id) DO UPDATE SET
                    embedding = COALESCE(EXCLUDED.embedding, event_embeddings.embedding),
                    nlp_score = COALESCE(EXCLUDED.nlp_score, event_embeddings.nlp_score),
                    nlp_confidence = COALESCE(EXCLUDED.nlp_confidence, event_embeddings.nlp_confidence),
                    tier = EXCLUDED.tier,
                    price_at_event = COALESCE(EXCLUDED.price_at_event, event_embeddings.price_at_event),
                    volume_zscore = COALESCE(EXCLUDED.volume_zscore, event_embeddings.volume_zscore),
                    fng_value = COALESCE(EXCLUDED.fng_value, event_embeddings.fng_value),
                    metadata = event_embeddings.metadata || EXCLUDED.metadata
                """,
                {
                    "id": id,
                    "asset": asset,
                    "ts": timestamp,
                    "source": source,
                    "title": title[:200],
                    "body_preview": body_preview[:200] if body_preview else None,
                    "chunk_text": chunk_text[:500],
                    "embedding": vec_str,
                    "nlp_score": nlp_score,
                    "nlp_confidence": nlp_confidence,
                    "tier": tier,
                    "price_at_event": price_at_event,
                    "volume_zscore": volume_zscore,
                    "fng_value": fng_value,
                    "volatility": volatility,
                    "metadata": json.dumps(metadata or {}),
                },
            )
            return True
        except Exception as e:
            logger.error("insert_event %s failed: %s", id, str(e)[:300])
            return False

    def label_outcomes(
        self,
        event_id: str,
        return_1h: float | None,
        return_6h: float | None,
        return_24h: float | None,
        max_drawdown_24h: float | None = None,
    ) -> bool:
        """Label an event with actual price outcomes."""
        store = self._get_store()
        try:
            store.execute(
                """
                UPDATE event_embeddings SET
                    return_1h = :r1h,
                    return_6h = :r6h,
                    return_24h = :r24h,
                    max_drawdown_24h = :mdd,
                    labeled_at = NOW()
                WHERE id = :id AND labeled_at IS NULL
                """,
                {
                    "id": event_id,
                    "r1h": return_1h,
                    "r6h": return_6h,
                    "r24h": return_24h,
                    "mdd": max_drawdown_24h,
                },
            )
            return True
        except Exception as e:
            logger.error("label_outcomes failed: %s", str(e)[:200])
            return False

    def get_unlabeled_events(self, min_age_hours: int = 6) -> list[dict]:
        """Get events old enough to label but not yet labeled."""
        store = self._get_store()
        rows = store.fetch_all(
            """
            SELECT id, asset, timestamp
            FROM event_embeddings
            WHERE labeled_at IS NULL
              AND timestamp < NOW() - INTERVAL '6 hours'
            ORDER BY timestamp ASC
            LIMIT 500
            """,
            {},
        )
        return rows

    def search_similar(
        self,
        embedding: np.ndarray,
        asset: str,
        top_k: int = 7,
    ) -> list[dict]:
        """Vector similarity search for labeled events."""
        store = self._get_store()
        vec_str = "[" + ",".join(str(float(v)) for v in embedding.flatten()) + "]"

        return store.fetch_all(
            """
            SELECT id, title, asset, timestamp,
                   return_1h, return_6h, return_24h, max_drawdown_24h,
                   nlp_score, tier,
                   1 - (embedding <=> CAST(:vec AS vector)) as similarity
            FROM event_embeddings
            WHERE labeled_at IS NOT NULL
              AND embedding IS NOT NULL
              AND asset = :asset
            ORDER BY embedding <=> CAST(:vec AS vector) ASC
            LIMIT :top_k
            """,
            {"vec": vec_str, "asset": asset, "top_k": top_k},
        )

    def stats(self) -> dict[str, Any]:
        """Get event store statistics."""
        store = self._get_store()
        rows = store.fetch_all(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN tier = '2' THEN 1 ELSE 0 END) as tier2,
                SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END) as has_embedding,
                SUM(CASE WHEN labeled_at IS NOT NULL THEN 1 ELSE 0 END) as labeled,
                MIN(timestamp) as earliest,
                MAX(timestamp) as latest
            FROM event_embeddings
            """,
            {},
        )
        r = rows[0] if rows else {}
        return {
            "total": r.get("total", 0),
            "tier2": r.get("tier2", 0),
            "has_embedding": r.get("has_embedding", 0),
            "labeled": r.get("labeled", 0),
            "earliest": r.get("earliest"),
            "latest": r.get("latest"),
        }

    def count_by_asset(self) -> dict[str, int]:
        store = self._get_store()
        rows = store.fetch_all(
            "SELECT asset, COUNT(*) as cnt FROM event_embeddings GROUP BY asset",
            {},
        )
        return {r["asset"]: r["cnt"] for r in rows}

    def create_ivfflat_index(self, lists: int = 50) -> None:
        """Create IVFFlat index for faster vector search.
        Call once after accumulating 1000+ labeled events.
        """
        store = self._get_store()
        store.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_event_emb_vector
            ON event_embeddings
            USING ivfflat (embedding vector_cosine_ops) WITH (lists = {lists})
            """,
            {},
        )
        logger.info("created IVFFlat index with %d lists", lists)


# Singleton
event_repository = EventRepository()
