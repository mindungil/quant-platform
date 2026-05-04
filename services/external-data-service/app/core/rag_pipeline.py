"""Two-tier sentiment RAG pipeline.

Tier 1 — CryptoBERT quick filter:
  Score every incoming news item. If |nlp_score| > threshold AND
  confidence > threshold, promote to Tier 2. Otherwise, store as
  Tier 1 event with NLP score only.

Tier 2 — Embedding + RAG similarity:
  1. Build chunk_text = title + body_preview (if any)
  2. Call external embedding server → get vector
  3. pgvector cosine similarity search → find top-k similar past events
  4. Aggregate past outcomes weighted by similarity
  5. Final impact = blend(model_prediction, rag_outcome)

The RAG outcome is weighted heavily because it's based on ACTUAL
historical price results, not model inference.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger("rag-pipeline")

# ─── Tier 1 Thresholds ──────────────────────────────────────
# Items with strong signal OR high confidence go to Tier 2
TIER2_NLP_THRESHOLD = 0.25      # |nlp_score| > this
TIER2_CONFIDENCE_THRESHOLD = 0.6  # confidence > this
# Also promote if severity keywords detected
TIER2_SEVERITY_THRESHOLD = 2.0   # severity >= this always goes to Tier 2

# ─── RAG Config ──────────────────────────────────────────────
RAG_TOP_K = 7                    # retrieve top-k similar events
RAG_MIN_SIMILARITY = 0.5         # cosine similarity threshold
RAG_OUTCOME_WEIGHT = 0.0         # DISABLED — data accumulation only, re-enable after 1000+ labeled events
RAG_RECENCY_HALFLIFE_DAYS = 180  # recent events weighted more


@dataclass
class TierResult:
    """Result of Tier 1 classification."""
    tier: str                    # "1" or "2"
    nlp_score: float
    nlp_confidence: float
    severity: float
    reason: str                  # why promoted to Tier 2


@dataclass
class RAGMatch:
    """A single similar past event from the vector store."""
    id: str
    title: str
    similarity: float            # cosine similarity [0, 1]
    return_6h: float | None
    return_24h: float | None
    asset: str
    timestamp: datetime
    days_ago: float


@dataclass
class RAGResult:
    """Aggregated RAG outcome."""
    matches: list[RAGMatch]
    weighted_return_6h: float | None
    weighted_return_24h: float | None
    confidence: float            # how confident we are in RAG result
    explanation: str


def classify_tier(
    nlp_score: float,
    nlp_confidence: float,
    severity: float,
) -> TierResult:
    """Decide whether a news item needs Tier 2 deep analysis.

    Tier 2 criteria (any one triggers):
      1. Strong NLP signal: |score| > 0.25 AND confidence > 0.6
      2. High severity keywords: severity >= 2.0
      3. Very high confidence extreme: confidence > 0.85 AND |score| > 0.15
    """
    reasons = []

    if abs(nlp_score) > TIER2_NLP_THRESHOLD and nlp_confidence > TIER2_CONFIDENCE_THRESHOLD:
        reasons.append(f"strong_signal(|{nlp_score:.2f}|>{TIER2_NLP_THRESHOLD}, conf={nlp_confidence:.2f})")

    if severity >= TIER2_SEVERITY_THRESHOLD:
        reasons.append(f"high_severity({severity:.1f}>={TIER2_SEVERITY_THRESHOLD})")

    if nlp_confidence > 0.85 and abs(nlp_score) > 0.15:
        reasons.append(f"high_conf_extreme(conf={nlp_confidence:.2f}, score={nlp_score:.2f})")

    tier = "2" if reasons else "1"
    return TierResult(
        tier=tier,
        nlp_score=nlp_score,
        nlp_confidence=nlp_confidence,
        severity=severity,
        reason=" | ".join(reasons) if reasons else "below_threshold",
    )


def build_chunk_text(title: str, body: str | None = None) -> str:
    """Build the text chunk for embedding.

    Strategy: title is always included (core information).
    Body preview (first ~200 chars) adds context when available.
    Total kept under ~250 chars to keep embedding focused.
    """
    text = title.strip()
    if body:
        # Take first 200 chars of body, cut at sentence boundary
        preview = body[:200].strip()
        last_period = preview.rfind(".")
        if last_period > 50:
            preview = preview[: last_period + 1]
        text = f"{text}. {preview}"
    return text


async def search_similar_events(
    store,
    embedding: np.ndarray,
    asset: str,
    top_k: int = RAG_TOP_K,
    min_similarity: float = RAG_MIN_SIMILARITY,
) -> list[RAGMatch]:
    """Search pgvector for similar past events with labeled outcomes.

    Uses cosine similarity. Only returns events that have been labeled
    (i.e., we know what actually happened to the price).
    """
    if embedding is None or len(embedding) == 0:
        return []

    vec_str = "[" + ",".join(str(float(v)) for v in embedding.flatten()) + "]"

    rows = store.fetch_all(
        """
        SELECT id, title, asset, timestamp,
               return_6h, return_24h,
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

    now = datetime.now(timezone.utc)
    matches = []
    for r in rows:
        sim = float(r["similarity"])
        if sim < min_similarity:
            continue
        ts = r["timestamp"]
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days_ago = (now - ts).total_seconds() / 86400

        matches.append(RAGMatch(
            id=r["id"],
            title=r["title"],
            similarity=sim,
            return_6h=r["return_6h"],
            return_24h=r["return_24h"],
            asset=r["asset"],
            timestamp=ts,
            days_ago=days_ago,
        ))
    return matches


def aggregate_rag_outcome(
    matches: list[RAGMatch],
    recency_halflife: float = RAG_RECENCY_HALFLIFE_DAYS,
) -> RAGResult:
    """Aggregate outcomes from similar past events.

    Weighting: similarity × recency_decay
    Recency decay: exp(-0.693 * days_ago / halflife)
    """
    if not matches:
        return RAGResult(
            matches=[],
            weighted_return_6h=None,
            weighted_return_24h=None,
            confidence=0.0,
            explanation="no_similar_events_found",
        )

    weights = []
    returns_6h = []
    returns_24h = []

    for m in matches:
        recency = np.exp(-0.693 * m.days_ago / recency_halflife)
        w = m.similarity * recency
        weights.append(w)
        if m.return_6h is not None:
            returns_6h.append((w, m.return_6h))
        if m.return_24h is not None:
            returns_24h.append((w, m.return_24h))

    def _weighted_avg(pairs: list[tuple[float, float]]) -> float | None:
        if not pairs:
            return None
        total_w = sum(w for w, _ in pairs)
        if total_w == 0:
            return None
        return sum(w * v for w, v in pairs) / total_w

    wr6 = _weighted_avg(returns_6h)
    wr24 = _weighted_avg(returns_24h)

    # Confidence based on: number of matches, average similarity, spread
    avg_sim = np.mean([m.similarity for m in matches])
    count_factor = min(len(matches) / RAG_TOP_K, 1.0)
    confidence = float(avg_sim * count_factor)

    top3 = sorted(matches, key=lambda m: -m.similarity)[:3]
    explanation = "; ".join(
        f"[{m.similarity:.2f}] {m.title[:60]} → 6h:{m.return_6h:+.2%}" if m.return_6h else f"[{m.similarity:.2f}] {m.title[:60]}"
        for m in top3
    )

    return RAGResult(
        matches=matches,
        weighted_return_6h=wr6,
        weighted_return_24h=wr24,
        confidence=confidence,
        explanation=explanation,
    )


def blend_impact(
    model_impact: float,
    rag_result: RAGResult,
    rag_weight: float = RAG_OUTCOME_WEIGHT,
) -> dict[str, Any]:
    """Blend model prediction with RAG historical outcome.

    If RAG has high confidence, it dominates.
    If RAG has no data, fall back entirely to model.
    """
    if rag_result.weighted_return_6h is None or rag_result.confidence < 0.3:
        return {
            "impact": model_impact,
            "rag_impact": None,
            "blend_weight": 0.0,
            "method": "model_only",
            "rag_confidence": rag_result.confidence,
            "rag_matches": len(rag_result.matches),
        }

    # Scale RAG return to z-score-like range (divide by typical daily vol ~3%)
    rag_impact = rag_result.weighted_return_6h / 0.03

    # Dynamic weight: higher RAG confidence → more RAG weight
    effective_rag_weight = rag_weight * rag_result.confidence
    effective_model_weight = 1.0 - effective_rag_weight

    blended = effective_model_weight * model_impact + effective_rag_weight * rag_impact

    return {
        "impact": float(blended),
        "model_impact": model_impact,
        "rag_impact": float(rag_impact),
        "blend_weight": float(effective_rag_weight),
        "method": "rag_blend",
        "rag_confidence": rag_result.confidence,
        "rag_matches": len(rag_result.matches),
        "rag_explanation": rag_result.explanation,
    }


def event_id(source: str, title: str, asset: str) -> str:
    """Deterministic event ID."""
    return hashlib.sha256(f"{source}:{asset}:{title}".encode()).hexdigest()[:16]
