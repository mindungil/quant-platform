"""Batch embedding background job.

Periodically processes unembedded Tier 2 events and creates
IVFFlat index when event count threshold is reached.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("batch-embedder")

BATCH_INTERVAL = int(os.getenv("BATCH_EMBED_INTERVAL_SECONDS", "600"))  # 10 min
IVFFLAT_THRESHOLD = int(os.getenv("IVFFLAT_INDEX_THRESHOLD", "1000"))
BATCH_SIZE = 50


class BatchEmbedder:
    """Background job that embeds unembedded Tier 2 events in bulk."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats = {
            "total_embedded": 0,
            "last_run": None,
            "last_batch_size": 0,
            "index_created": False,
            "errors": 0,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("batch_embedder_started", extra={"interval": BATCH_INTERVAL})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        await asyncio.sleep(30)  # stagger startup
        while self._running:
            try:
                count = await self._process_batch()
                self._stats["last_run"] = datetime.now(timezone.utc).isoformat()
                self._stats["last_batch_size"] = count
                if count > 0:
                    logger.info("batch_embed_complete", extra={"count": count})
                    await self._maybe_create_index()
            except Exception as exc:
                self._stats["errors"] += 1
                logger.exception("batch_embed_error", extra={"error": str(exc)[:200]})
            await asyncio.sleep(BATCH_INTERVAL)

    async def _process_batch(self) -> int:
        """Fetch unembedded Tier 2 events and embed them."""
        from app.db.event_repo import event_repository
        from app.core.embedding_client import embedding_client
        from app.core.rag_pipeline import build_chunk_text

        if not embedding_client.is_available:
            health = await embedding_client.health_check()
            if not health:
                return 0

        store = event_repository._get_store()
        rows = store.fetch_all(
            """
            SELECT id, title, body_preview, chunk_text
            FROM event_embeddings
            WHERE tier = '2'
              AND embedding IS NULL
            ORDER BY timestamp DESC
            LIMIT :limit
            """,
            {"limit": BATCH_SIZE},
        )

        if not rows:
            return 0

        texts = []
        ids = []
        for r in rows:
            text = r.get("chunk_text") or build_chunk_text(
                r.get("title", ""), r.get("body_preview")
            )
            texts.append(text)
            ids.append(r["id"])

        embeddings = await embedding_client.embed(texts)
        if embeddings is None:
            return 0

        import numpy as np
        embedded = 0
        for i, eid in enumerate(ids):
            if i < len(embeddings):
                vec = embeddings[i]
                vec_str = "[" + ",".join(str(float(v)) for v in vec.flatten()) + "]"
                try:
                    store.execute(
                        """
                        UPDATE event_embeddings
                        SET embedding = CAST(:vec AS vector)
                        WHERE id = :id AND embedding IS NULL
                        """,
                        {"vec": vec_str, "id": eid},
                    )
                    embedded += 1
                except Exception as exc:
                    logger.warning("embed_update_failed", extra={"id": eid, "error": str(exc)[:100]})

        self._stats["total_embedded"] += embedded
        return embedded

    async def _maybe_create_index(self) -> None:
        """Auto-create IVFFlat index when enough events accumulated."""
        if self._stats["index_created"]:
            return

        from app.db.event_repo import event_repository
        try:
            stats = event_repository.stats()
            has_embedding = stats.get("has_embedding", 0)
            if has_embedding and has_embedding >= IVFFLAT_THRESHOLD:
                lists = max(50, has_embedding // 20)  # ~20 events per list
                event_repository.create_ivfflat_index(lists=lists)
                self._stats["index_created"] = True
                logger.info("ivfflat_index_auto_created", extra={
                    "events": has_embedding, "lists": lists,
                })
        except Exception as exc:
            logger.warning("ivfflat_check_failed", extra={"error": str(exc)[:100]})

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# Singleton
batch_embedder = BatchEmbedder()
