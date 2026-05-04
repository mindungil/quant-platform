"""Sentiment data repository — TimescaleDB read/write for sentiment tables."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
from shared.persistence import SqlStore as SQLStore

logger = logging.getLogger("sentiment-repo")

_MARKET_DB_URL = os.getenv(
    "POSTGRES_URL_MARKET",
    os.getenv("TIMESCALE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/market"),
)


class SentimentRepository:
    def __init__(self) -> None:
        self._store = SQLStore(_MARKET_DB_URL)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_items (
                id TEXT NOT NULL,
                asset TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                source TEXT NOT NULL,
                source_id TEXT,
                title TEXT NOT NULL,
                body TEXT,
                nlp_score DOUBLE PRECISION,
                nlp_model TEXT,
                nlp_confidence DOUBLE PRECISION,
                community_score DOUBLE PRECISION,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (id, asset)
            )
        """)
        # Index for time-range queries (aggregation, cleanup)
        self._store.execute("""
            CREATE INDEX IF NOT EXISTS idx_sentiment_items_ts
            ON sentiment_items (asset, timestamp DESC)
        """)
        self._store.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_hourly (
                asset TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                nlp_mean DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                nlp_median DOUBLE PRECISION,
                nlp_std DOUBLE PRECISION,
                nlp_count INTEGER NOT NULL DEFAULT 0,
                news_score DOUBLE PRECISION,
                social_score DOUBLE PRECISION,
                community_score DOUBLE PRECISION,
                total_items INTEGER NOT NULL DEFAULT 0,
                bullish_count INTEGER NOT NULL DEFAULT 0,
                bearish_count INTEGER NOT NULL DEFAULT 0,
                neutral_count INTEGER NOT NULL DEFAULT 0,
                fng_value INTEGER,
                lunarcrush_galaxy DOUBLE PRECISION,
                composite_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                PRIMARY KEY (asset, timestamp)
            )
        """)

    # ─── Items ────────────────────────────────────────────────

    def insert_items(self, items: list[dict]) -> int:
        """Insert sentiment items, skip duplicates. Returns count inserted."""
        if not items:
            return 0
        inserted = 0
        for item in items:
            try:
                self._store.execute(
                    """
                    INSERT INTO sentiment_items
                        (id, asset, timestamp, source, source_id, title, body,
                         nlp_score, nlp_model, nlp_confidence, community_score, metadata)
                    VALUES
                        (:id, :asset, :ts, :source, :source_id, :title, :body,
                         :nlp_score, :nlp_model, :nlp_confidence, :community_score,
                         CAST(:metadata AS JSONB))
                    ON CONFLICT (id, asset) DO UPDATE SET
                        nlp_score = COALESCE(EXCLUDED.nlp_score, sentiment_items.nlp_score),
                        nlp_model = COALESCE(EXCLUDED.nlp_model, sentiment_items.nlp_model),
                        nlp_confidence = COALESCE(EXCLUDED.nlp_confidence, sentiment_items.nlp_confidence),
                        community_score = COALESCE(EXCLUDED.community_score, sentiment_items.community_score)
                    """,
                    {
                        "id": item["id"],
                        "asset": item["asset"],
                        "ts": item["timestamp"],
                        "source": item["source"],
                        "source_id": item.get("source_id"),
                        "title": item["title"],
                        "body": item.get("body"),
                        "nlp_score": item.get("nlp_score"),
                        "nlp_model": item.get("nlp_model"),
                        "nlp_confidence": item.get("nlp_confidence"),
                        "community_score": item.get("community_score"),
                        "metadata": str(item.get("metadata", {})).replace("'", '"'),
                    },
                )
                inserted += 1
            except Exception:
                pass  # duplicate or constraint violation
        return inserted

    def get_unscored_items(self, limit: int = 100) -> list[dict]:
        """Get items without NLP score for batch scoring."""
        rows = self._store.fetch_all(
            """
            SELECT id, asset, timestamp, source, title, body, community_score
            FROM sentiment_items
            WHERE nlp_score IS NULL
            ORDER BY timestamp DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        return [dict(r) for r in rows]

    def update_nlp_score(self, item_id: str, asset: str, timestamp: str,
                         score: float, model: str, confidence: float) -> None:
        """Update NLP score for a scored item."""
        self._store.execute(
            """
            UPDATE sentiment_items
            SET nlp_score = :score, nlp_model = :model, nlp_confidence = :confidence
            WHERE id = :id AND asset = :asset
            """,
            {"score": score, "model": model, "confidence": confidence,
             "id": item_id, "asset": asset},
        )

    # ─── Hourly Aggregation ───────────────────────────────────

    def aggregate_hour(self, asset: str, hour_start: datetime,
                       fng_value: int | None = None) -> dict:
        """Compute hourly aggregate from items in the given hour."""
        rows = self._store.fetch_all(
            """
            SELECT nlp_score, community_score, source
            FROM sentiment_items
            WHERE asset = :asset
              AND timestamp >= :start
              AND timestamp < :start + INTERVAL '1 hour'
            """,
            {"asset": asset, "start": hour_start},
        )
        items = [dict(r) for r in rows]
        if not items:
            return {}

        # Separate by source type
        news_sources = {"cryptopanic", "coindesk", "cointelegraph", "newsapi",
                        "decrypt", "theblock", "thedefiant"}

        # Use nlp_score if available, else community_score, else 0
        def score(item: dict) -> float:
            return item.get("nlp_score") or item.get("community_score") or 0.0

        all_scores = [score(i) for i in items]
        news_scores = [score(i) for i in items if i["source"] in news_sources]
        community_scores = [i["community_score"] for i in items if i.get("community_score") is not None]

        bullish = sum(1 for s in all_scores if s > 0.1)
        bearish = sum(1 for s in all_scores if s < -0.1)
        neutral = len(all_scores) - bullish - bearish

        nlp_scored = [i["nlp_score"] for i in items if i.get("nlp_score") is not None]

        # Composite score — weighted blend of available signals
        # Weights reflect actual data availability (no Reddit/social)
        fng_norm = (fng_value - 50) / 50 if fng_value is not None else 0.0
        nlp_avg = float(np.mean(nlp_scored)) if nlp_scored else 0.0
        news_avg = float(np.mean(news_scores)) if news_scores else 0.0
        comm_avg = float(np.mean(community_scores)) if community_scores else 0.0

        # Volume surprise: more news items than usual amplifies the signal
        vol_factor = min(len(items) / max(8, 1), 2.0)  # cap at 2x

        # Bull/bear ratio as additional signal
        total_directional = bullish + bearish
        sentiment_ratio = (bullish - bearish) / total_directional if total_directional > 0 else 0.0

        composite = (
            0.40 * nlp_avg                     # NLP is our strongest signal
            + 0.20 * (fng_norm * -1)           # FNG contrarian (high greed → cautious)
            + 0.15 * news_avg                  # news source aggregate
            + 0.10 * comm_avg                  # keyword-based community score
            + 0.15 * sentiment_ratio           # bull/bear ratio from scored items
        )
        # Apply volume amplifier: if unusually many items, signal is more meaningful
        if vol_factor > 1.2:
            composite *= min(vol_factor, 1.5)

        agg = {
            "asset": asset,
            "timestamp": hour_start.isoformat(),
            "nlp_mean": round(nlp_avg, 4),
            "nlp_median": round(float(np.median(nlp_scored)), 4) if nlp_scored else None,
            "nlp_std": round(float(np.std(nlp_scored)), 4) if len(nlp_scored) > 1 else None,
            "nlp_count": len(nlp_scored),
            "news_score": round(news_avg, 4) if news_scores else None,
            "social_score": None,  # no social sources currently active
            "community_score": round(comm_avg, 4) if community_scores else None,
            "total_items": len(items),
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "fng_value": fng_value,
            "composite_score": round(composite, 4),
        }

        # Upsert
        self._store.execute(
            """
            INSERT INTO sentiment_hourly
                (asset, timestamp, nlp_mean, nlp_median, nlp_std, nlp_count,
                 news_score, social_score, community_score,
                 total_items, bullish_count, bearish_count, neutral_count,
                 fng_value, composite_score)
            VALUES
                (:asset, :timestamp, :nlp_mean, :nlp_median, :nlp_std, :nlp_count,
                 :news_score, :social_score, :community_score,
                 :total_items, :bullish_count, :bearish_count, :neutral_count,
                 :fng_value, :composite_score)
            ON CONFLICT (asset, timestamp) DO UPDATE SET
                nlp_mean = EXCLUDED.nlp_mean,
                nlp_count = EXCLUDED.nlp_count,
                news_score = EXCLUDED.news_score,
                social_score = EXCLUDED.social_score,
                community_score = EXCLUDED.community_score,
                total_items = EXCLUDED.total_items,
                bullish_count = EXCLUDED.bullish_count,
                bearish_count = EXCLUDED.bearish_count,
                neutral_count = EXCLUDED.neutral_count,
                fng_value = EXCLUDED.fng_value,
                composite_score = EXCLUDED.composite_score
            """,
            agg,
        )
        return agg

    # ─── Query ────────────────────────────────────────────────

    def get_hourly(self, asset: str, limit: int = 168) -> list[dict]:
        """Get recent hourly sentiment for an asset."""
        rows = self._store.fetch_all(
            """
            SELECT * FROM sentiment_hourly
            WHERE asset = :asset
            ORDER BY timestamp DESC
            LIMIT :limit
            """,
            {"asset": asset, "limit": limit},
        )
        return [dict(r) for r in rows]

    def get_composite_series(self, asset: str, limit: int = 2000) -> list[tuple]:
        """Get (timestamp, composite_score) pairs for feature engine."""
        rows = self._store.fetch_all(
            """
            SELECT timestamp, composite_score
            FROM sentiment_hourly
            WHERE asset = :asset
            ORDER BY timestamp ASC
            LIMIT :limit
            """,
            {"asset": asset, "limit": limit},
        )
        return [(r["timestamp"], r["composite_score"]) for r in rows]

    def item_count(self) -> int:
        rows = self._store.fetch_all("SELECT COUNT(*) as cnt FROM sentiment_items", {})
        return rows[0]["cnt"] if rows else 0

    def hourly_count(self) -> int:
        rows = self._store.fetch_all("SELECT COUNT(*) as cnt FROM sentiment_hourly", {})
        return rows[0]["cnt"] if rows else 0

    def cleanup_old_items(self, days: int = 365) -> int:
        """Delete raw sentiment items older than `days`. Hourly aggregates are kept."""
        rows = self._store.fetch_all(
            "DELETE FROM sentiment_items WHERE timestamp < NOW() - INTERVAL '365 days' RETURNING id",
            {},
        )
        return len(rows)

    def db_stats(self) -> dict:
        """Return storage stats for monitoring."""
        rows = self._store.fetch_all("""
            SELECT relname,
                   pg_total_relation_size(relid) as total_bytes,
                   n_live_tup as row_count
            FROM pg_catalog.pg_stat_user_tables
            WHERE relname LIKE 'sentiment%%'
            ORDER BY relname
        """, {})
        return {r["relname"]: {"bytes": r["total_bytes"], "rows": r["row_count"]} for r in rows}


# Singleton
sentiment_repository = SentimentRepository()
