"""Real-time streaming RAG scorer.

Processes incoming sentiment events asynchronously, computes RAG-enhanced
impact scores, and publishes results to NATS for consumption by the
signal pipeline.

Flow:
  1. Receive event from sentiment collector
  2. Classify tier (NLP threshold)
  3. If Tier 2: embed + RAG search + blend impact
  4. Publish scored event to NATS topic
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger("stream-scorer")

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
SCORE_TOPIC = "sentiment.scored"
MIN_IMPACT_THRESHOLD = 0.15  # Only publish events above this impact


@dataclass
class ScoredEvent:
    """A fully scored sentiment event ready for signal consumption."""
    event_id: str
    asset: str
    timestamp: str
    source: str
    title: str
    nlp_score: float
    nlp_confidence: float
    tier: str
    model_impact: float
    rag_impact: float | None
    blended_impact: float
    rag_confidence: float
    rag_matches: int
    direction: str  # "BULLISH" | "BEARISH" | "NEUTRAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "asset": self.asset,
            "timestamp": self.timestamp,
            "source": self.source,
            "title": self.title[:100],
            "nlp_score": round(self.nlp_score, 4),
            "nlp_confidence": round(self.nlp_confidence, 3),
            "tier": self.tier,
            "model_impact": round(self.model_impact, 4),
            "rag_impact": round(self.rag_impact, 4) if self.rag_impact is not None else None,
            "blended_impact": round(self.blended_impact, 4),
            "rag_confidence": round(self.rag_confidence, 3),
            "rag_matches": self.rag_matches,
            "direction": self.direction,
        }


class StreamScorer:
    """Async RAG scorer that processes events and publishes to NATS."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats = {
            "events_received": 0,
            "events_scored": 0,
            "events_published": 0,
            "tier2_count": 0,
            "errors": 0,
            "avg_latency_ms": 0.0,
        }
        self._latencies: list[float] = []

    async def start(self, n_workers: int = 2) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.gather(
            *[asyncio.create_task(self._worker(i)) for i in range(n_workers)]
        )
        logger.info("stream_scorer_started", extra={"workers": n_workers})

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def enqueue(self, event: dict) -> bool:
        """Add a raw sentiment event to the scoring queue."""
        self._stats["events_received"] += 1
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            logger.warning("scorer_queue_full")
            return False

    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine that processes events from the queue."""
        import time
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

            t0 = time.monotonic()
            try:
                scored = await self._score_event(event)
                if scored and abs(scored.blended_impact) >= MIN_IMPACT_THRESHOLD:
                    await self._publish(scored)
                    self._stats["events_published"] += 1
                self._stats["events_scored"] += 1

                latency_ms = (time.monotonic() - t0) * 1000
                self._latencies.append(latency_ms)
                if len(self._latencies) > 100:
                    self._latencies = self._latencies[-100:]
                self._stats["avg_latency_ms"] = round(
                    sum(self._latencies) / len(self._latencies), 1
                )
            except Exception as exc:
                self._stats["errors"] += 1
                logger.warning("score_event_failed", extra={
                    "worker": worker_id,
                    "error": str(exc)[:200],
                })

    async def _score_event(self, event: dict) -> ScoredEvent | None:
        """Score a single event through the RAG pipeline."""
        from app.core.rag_pipeline import (
            classify_tier,
            build_chunk_text,
            search_similar_events,
            aggregate_rag_outcome,
            blend_impact,
            event_id as make_event_id,
        )
        from app.core.embedding_client import embedding_client
        from app.db.event_repo import event_repository

        title = event.get("title", "")
        asset = event.get("asset", "BTC")
        source = event.get("source", "unknown")
        nlp_score = float(event.get("nlp_score", 0.0))
        nlp_confidence = float(event.get("nlp_confidence", 0.5))
        severity = float(event.get("severity", 1.0))

        # Tier classification
        tier_result = classify_tier(nlp_score, nlp_confidence, severity)

        # Model-only impact (simple z-score from NLP)
        model_impact = nlp_score * nlp_confidence * severity

        rag_impact = None
        rag_confidence = 0.0
        rag_matches = 0

        if tier_result.tier == "2":
            self._stats["tier2_count"] += 1
            chunk_text = build_chunk_text(title, event.get("body"))

            # Embed
            embedding = await embedding_client.embed(chunk_text)
            if embedding is not None:
                # Store event with embedding
                eid = make_event_id(source, title, asset)
                event_repository.insert_event(
                    id=eid,
                    asset=asset,
                    timestamp=datetime.now(timezone.utc),
                    source=source,
                    title=title,
                    chunk_text=chunk_text,
                    tier="2",
                    nlp_score=nlp_score,
                    nlp_confidence=nlp_confidence,
                    embedding=embedding[0] if len(embedding.shape) > 1 else embedding,
                )

                # RAG search
                store = event_repository._get_store()
                matches = await search_similar_events(
                    store, embedding[0] if len(embedding.shape) > 1 else embedding, asset
                )
                rag_result = aggregate_rag_outcome(matches)
                rag_confidence = rag_result.confidence
                rag_matches = len(rag_result.matches)

                blend = blend_impact(model_impact, rag_result, rag_weight=0.3)
                model_impact = blend["impact"]
                rag_impact = blend.get("rag_impact")
        else:
            # Tier 1: store without embedding
            eid = make_event_id(source, title, asset)
            event_repository.insert_event(
                id=eid,
                asset=asset,
                timestamp=datetime.now(timezone.utc),
                source=source,
                title=title,
                chunk_text=title,
                tier="1",
                nlp_score=nlp_score,
                nlp_confidence=nlp_confidence,
            )

        direction = "BULLISH" if model_impact > 0.1 else ("BEARISH" if model_impact < -0.1 else "NEUTRAL")

        return ScoredEvent(
            event_id=event.get("id", ""),
            asset=asset,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=source,
            title=title,
            nlp_score=nlp_score,
            nlp_confidence=nlp_confidence,
            tier=tier_result.tier,
            model_impact=model_impact,
            rag_impact=rag_impact,
            blended_impact=model_impact,
            rag_confidence=rag_confidence,
            rag_matches=rag_matches,
            direction=direction,
        )

    async def _publish(self, scored: ScoredEvent) -> None:
        """Publish scored event to NATS."""
        try:
            import nats as nats_lib
            nc = await nats_lib.connect(NATS_URL)
            payload = json.dumps(scored.to_dict(), default=str).encode()
            await nc.publish(SCORE_TOPIC, payload)
            await nc.flush()
            await nc.close()
        except Exception as exc:
            logger.debug("nats_publish_failed", extra={"error": str(exc)[:100]})

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# Singleton
stream_scorer = StreamScorer()
