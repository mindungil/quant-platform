#!/usr/bin/env python3
"""Two-tier RAG sentiment pipeline.

Extends the base sentiment pipeline with:
  Tier 1: CryptoBERT scores every item. Low-impact → store & done.
  Tier 2: High-impact items → external embedding → RAG similarity search
          → blend model prediction with historical outcome.

Runs alongside the base pipeline (which handles collection + hourly aggregation).
This script processes the RAG layer on already-collected items.

Usage:
  python scripts/rag_sentiment_pipeline.py              # one-shot
  python scripts/rag_sentiment_pipeline.py --label-only  # only label past events
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import httpx

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "external-data-service"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("rag-pipeline")


async def process_tier_classification() -> dict:
    """Classify recent sentiment items into Tier 1/2 and store in event_embeddings."""
    from app.db.sentiment_repo import sentiment_repository as sent_repo
    from app.db.event_repo import event_repository as event_repo
    from app.core.rag_pipeline import classify_tier, build_chunk_text, event_id
    from app.core.impact_model import extract_severity, extract_source_weight

    store = sent_repo._store

    # Get items that haven't been classified yet (no time window — process all backlog)
    recent_items = store.fetch_all(
        """
        SELECT si.id, si.asset, si.timestamp, si.source, si.title, si.body,
               si.nlp_score, si.nlp_confidence, si.community_score
        FROM sentiment_items si
        LEFT JOIN event_embeddings ee ON ee.id = si.id AND ee.asset = si.asset
        WHERE si.nlp_score IS NOT NULL
          AND ee.id IS NULL
        ORDER BY si.timestamp DESC
        LIMIT 500
        """,
        {},
    )

    if not recent_items:
        logger.info("no new items to classify")
        return {"classified": 0, "tier1": 0, "tier2": 0}

    tier1_count = 0
    tier2_count = 0
    tier2_items = []

    from app.core.event_classifier import classify_event
    from shared.portfolio.sentiment_risk_filter import register_event

    risk_events = 0
    for item in recent_items:
        nlp_score = float(item["nlp_score"] or 0)
        nlp_conf = float(item["nlp_confidence"] or 0.5)
        severity = extract_severity(item["title"])

        result = classify_tier(nlp_score, nlp_conf, severity)

        # Event classification → risk filter
        ec = classify_event(item["title"], nlp_score, nlp_conf, item.get("body"))
        if ec.signal_dampening > 0.05:
            register_event(
                event_type=ec.event_type.value,
                severity=ec.severity,
                dampening=ec.signal_dampening,
                asset=item["asset"],
                title=item["title"],
                timestamp=item["timestamp"],
            )
            risk_events += 1
        chunk = build_chunk_text(item["title"], item.get("body"))
        eid = item["id"]

        event_repo.insert_event(
            id=eid,
            asset=item["asset"],
            timestamp=item["timestamp"],
            source=item["source"],
            title=item["title"],
            chunk_text=chunk,
            tier=result.tier,
            nlp_score=nlp_score,
            nlp_confidence=nlp_conf,
            body_preview=item.get("body"),
            metadata={"severity": severity, "tier_reason": result.reason},
        )

        if result.tier == "2":
            tier2_count += 1
            tier2_items.append((eid, chunk, item["asset"]))
        else:
            tier1_count += 1

    logger.info(
        "classified %d items: tier1=%d, tier2=%d",
        len(recent_items), tier1_count, tier2_count,
    )

    return {
        "classified": len(recent_items),
        "tier1": tier1_count,
        "tier2": tier2_count,
        "tier2_items": tier2_items,
    }


async def process_embeddings(tier2_items: list[tuple[str, str, str]]) -> int:
    """Send Tier 2 items to embedding server and store vectors."""
    if not tier2_items:
        return 0

    from app.core.embedding_client import embedding_client
    from app.db.event_repo import event_repository as event_repo

    # Health check first
    is_healthy = await embedding_client.health_check()
    if not is_healthy:
        logger.warning("embedding server unavailable, skipping embedding step")
        return 0

    # Batch embed all Tier 2 texts
    texts = [chunk for _, chunk, _ in tier2_items]
    embeddings = await embedding_client.embed(texts)

    if embeddings is None:
        logger.error("embedding request failed")
        return 0

    # Store embeddings
    stored = 0
    for i, (eid, chunk, asset) in enumerate(tier2_items):
        vec = embeddings[i]
        store = event_repo._get_store()
        vec_str = "[" + ",".join(str(float(v)) for v in vec) + "]"
        try:
            store.execute(
                """
                UPDATE event_embeddings
                SET embedding = CAST(:vec AS vector)
                WHERE id = :id
                """,
                {"vec": vec_str, "id": eid},
            )
            stored += 1
        except Exception as e:
            logger.error("store embedding %s: %s", eid, str(e)[:100])

    logger.info("embedded and stored %d/%d tier2 items", stored, len(tier2_items))
    return stored


async def process_rag_search(tier2_items: list[tuple[str, str, str]]) -> list[dict]:
    """Run RAG similarity search for Tier 2 items with embeddings."""
    if not tier2_items:
        return []

    from app.db.event_repo import event_repository as event_repo
    from app.core.rag_pipeline import (
        search_similar_events, aggregate_rag_outcome, blend_impact,
    )
    from app.core.impact_model import impact_model

    results = []
    store = event_repo._get_store()

    for eid, chunk, asset in tier2_items:
        # Get the stored embedding
        rows = store.fetch_all(
            "SELECT embedding, nlp_score, nlp_confidence, title FROM event_embeddings WHERE id = :id",
            {"id": eid},
        )
        if not rows or rows[0]["embedding"] is None:
            continue

        import numpy as np
        # pgvector returns string representation
        emb_raw = rows[0]["embedding"]
        if isinstance(emb_raw, str):
            emb = np.array([float(x) for x in emb_raw.strip("[]").split(",")], dtype=np.float32)
        else:
            emb = np.array(emb_raw, dtype=np.float32)

        # Search similar events
        matches = await search_similar_events(store, emb, asset)

        rag_result = aggregate_rag_outcome(
            [__import__("app.core.rag_pipeline", fromlist=["RAGMatch"]).RAGMatch(
                id=m["id"],
                title=m["title"],
                similarity=float(m["similarity"]),
                return_6h=m["return_6h"],
                return_24h=m["return_24h"],
                asset=m["asset"],
                timestamp=m["timestamp"],
                days_ago=(datetime.now(timezone.utc) - m["timestamp"].replace(tzinfo=timezone.utc)
                          if m["timestamp"].tzinfo is None else
                          datetime.now(timezone.utc) - m["timestamp"]).total_seconds() / 86400,
            ) for m in matches]
        )

        # Get model prediction for blending
        nlp_score = float(rows[0]["nlp_score"] or 0)
        model_pred = impact_model.predict(
            nlp_score=nlp_score,
            nlp_confidence=float(rows[0]["nlp_confidence"] or 0.5),
            text=rows[0]["title"],
        )
        model_impact = model_pred.get("impact", nlp_score)

        blended = blend_impact(model_impact, rag_result)

        logger.info(
            "RAG %s [%s]: model=%.3f rag=%.3f blend=%.3f (matches=%d, conf=%.2f)",
            eid[:8], asset,
            model_impact,
            blended.get("rag_impact", 0) or 0,
            blended["impact"],
            blended["rag_matches"],
            blended["rag_confidence"],
        )

        # Store RAG result in metadata
        store.execute(
            """
            UPDATE event_embeddings SET
                metadata = metadata || CAST(:rag_meta AS jsonb)
            WHERE id = :id
            """,
            {
                "id": eid,
                "rag_meta": str({
                    "rag_impact": blended.get("rag_impact"),
                    "blended_impact": blended["impact"],
                    "rag_confidence": blended["rag_confidence"],
                    "rag_matches": blended["rag_matches"],
                    "method": blended["method"],
                }).replace("'", '"').replace("None", "null"),
            },
        )

        results.append(blended)

    return results


async def _fetch_binance_klines(client: httpx.AsyncClient, symbol: str, start_ms: int, limit: int = 25) -> list[float]:
    """Fetch hourly close prices from Binance starting at start_ms."""
    resp = await client.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": symbol, "interval": "1h", "startTime": start_ms, "limit": limit},
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    return [float(k[4]) for k in resp.json()]  # index 4 = close price


async def label_past_events() -> int:
    """Auto-label events older than 24h with actual price outcomes.

    Uses Binance API for recent data (no dependency on local OHLCV files),
    falls back to local OHLCV for older historical events.
    """
    from app.db.event_repo import event_repository as event_repo
    import httpx

    unlabeled = event_repo.get_unlabeled_events(min_age_hours=24)
    if not unlabeled:
        logger.info("no events to label")
        return 0

    labeled = 0
    # Try local OHLCV first, Binance API as fallback
    price_cache = {}
    try:
        from shared.backtest.alpha_validator import load_ohlcv_stitched
        has_local = True
    except ImportError:
        has_local = False

    async with httpx.AsyncClient() as client:
        for event in unlabeled:
            asset_pair = f"{event['asset']}USDT"
            ts = event["timestamp"]
            if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                from datetime import timezone as tz
                ts = ts.replace(tzinfo=tz.utc)

            try:
                prices = None

                # Strategy 1: try local OHLCV
                if has_local and asset_pair not in price_cache:
                    try:
                        df = load_ohlcv_stitched(asset_pair)
                        price_cache[asset_pair] = df
                    except Exception:
                        price_cache[asset_pair] = None

                if has_local and price_cache.get(asset_pair) is not None:
                    df = price_cache[asset_pair]
                    idx = df.index.get_indexer([ts], method="nearest")[0]
                    if 0 <= idx < len(df) - 24:
                        prices = [float(df.iloc[idx + j]["close"]) for j in range(25)]

                # Strategy 2: Binance API fallback
                if prices is None:
                    start_ms = int(ts.timestamp() * 1000)
                    prices = await _fetch_binance_klines(client, asset_pair, start_ms, 25)

                if not prices or len(prices) < 25:
                    continue

                price_at = prices[0]
                ret_1h = (prices[1] - price_at) / price_at
                ret_6h = (prices[6] - price_at) / price_at
                ret_24h = (prices[24] - price_at) / price_at

                # Max drawdown in 24h window
                running_max = price_at
                max_dd = 0.0
                for p in prices[1:]:
                    running_max = max(running_max, p)
                    dd = (p - running_max) / running_max
                    max_dd = min(max_dd, dd)

                event_repo.label_outcomes(
                    event_id=event["id"],
                    return_1h=ret_1h,
                    return_6h=ret_6h,
                    return_24h=ret_24h,
                    max_drawdown_24h=max_dd,
                )

                event_repo._get_store().execute(
                    "UPDATE event_embeddings SET price_at_event = :p WHERE id = :id",
                    {"p": price_at, "id": event["id"]},
                )

                labeled += 1
            except Exception as e:
                logger.debug("label %s: %s", event["id"][:8], str(e)[:80])

    logger.info("labeled %d/%d events with price outcomes", labeled, len(unlabeled))
    return labeled


async def run_full_pipeline() -> dict:
    """Run the complete Tier 1/2 RAG pipeline."""
    # Step 1: Classify recent items into tiers
    classification = await process_tier_classification()

    # Step 2: Embed Tier 2 items
    tier2_items = classification.get("tier2_items", [])
    embedded = await process_embeddings(tier2_items)

    # Step 3: RAG search for Tier 2 items (only if they have embeddings)
    rag_results = await process_rag_search(tier2_items) if embedded > 0 else []

    # Step 4: Label past events with actual price outcomes
    labeled = await label_past_events()

    # Step 5: Retrain impact model if enough labeled data accumulated
    retrained = False
    try:
        retrained = maybe_retrain_impact_model()
    except Exception as e:
        logger.debug("impact model retrain check: %s", str(e)[:100])

    return {
        "classified": classification["classified"],
        "tier1": classification["tier1"],
        "tier2": classification["tier2"],
        "embedded": embedded,
        "rag_searches": len(rag_results),
        "labeled": labeled,
        "retrained": retrained,
    }


_RETRAIN_MIN_LABELED = 200       # minimum labeled events to attempt retraining
_RETRAIN_INTERVAL_HOURS = 168    # retrain at most once per week
_last_retrain: float = 0


def maybe_retrain_impact_model() -> bool:
    """Retrain impact model if enough new labeled data has accumulated.

    Uses event_embeddings labeled data (not sentiment_items) since those
    have actual forward returns as labels.
    """
    import time
    global _last_retrain

    if time.time() - _last_retrain < _RETRAIN_INTERVAL_HOURS * 3600:
        return False

    from app.db.event_repo import event_repository as event_repo
    store = event_repo._get_store()

    # Check if we have enough labeled data
    rows = store.fetch_all(
        "SELECT COUNT(*) as cnt FROM event_embeddings WHERE labeled_at IS NOT NULL",
        {},
    )
    n_labeled = rows[0]["cnt"] if rows else 0
    if n_labeled < _RETRAIN_MIN_LABELED:
        logger.debug("impact model: %d labeled events (need %d)", n_labeled, _RETRAIN_MIN_LABELED)
        return False

    # Load labeled events with NLP scores
    labeled = store.fetch_all(
        """
        SELECT title, source, nlp_score, nlp_confidence, timestamp,
               return_6h, fng_value
        FROM event_embeddings
        WHERE labeled_at IS NOT NULL
          AND nlp_score IS NOT NULL
          AND return_6h IS NOT NULL
        ORDER BY timestamp ASC
        """,
        {},
    )
    if len(labeled) < _RETRAIN_MIN_LABELED:
        return False

    from app.core.impact_model import impact_model, extract_features, extract_severity, extract_source_weight
    import numpy as np

    X_list, y_list = [], []
    for item in labeled:
        ts = item["timestamp"]
        features = extract_features(
            nlp_score=float(item["nlp_score"]),
            nlp_confidence=float(item.get("nlp_confidence") or 0.5),
            severity=extract_severity(item.get("title", "")),
            source_weight=extract_source_weight(item.get("source", "")),
            volume_zscore=0.0,
            hour_of_day=ts.hour if hasattr(ts, "hour") else 12,
            day_of_week=ts.weekday() if hasattr(ts, "weekday") else 3,
            fng_value=item.get("fng_value"),
        )
        X_list.append(features)
        y_list.append(float(item["return_6h"]))

    X = np.array(X_list)
    y = np.array(y_list)

    result = impact_model.train(X, y)
    _last_retrain = time.time()

    logger.info("impact model retrain: %s", result)
    return result.get("status") == "trained"


def main():
    ap = argparse.ArgumentParser(description="Two-tier RAG sentiment pipeline")
    ap.add_argument("--label-only", action="store_true", help="Only label past events")
    args = ap.parse_args()

    if args.label_only:
        labeled = asyncio.run(label_past_events())
        print(f"Labeled: {labeled}")
    else:
        result = asyncio.run(run_full_pipeline())
        print(f"Done: {result}")


if __name__ == "__main__":
    main()
