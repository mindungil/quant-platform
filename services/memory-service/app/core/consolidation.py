"""DREAM-style memory consolidation: archive low-value memories, detect contradictions, prune old records."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from itertools import groupby
from operator import attrgetter

from app.models.memory import MemoryRecord

UTC = timezone.utc
logger = logging.getLogger("memory-service")


def consolidate_memories(user_id: str, repository, *, dry_run: bool = False) -> dict:
    """Consolidate memories older than 7 days.

    Groups by (asset, strategy_id), keeps top-5 per group by outcome_sharpe,
    archives the rest.  Also detects contradictions (same asset, opposing
    actions with similar signal_score but different outcomes).

    Returns ``{archived: int, contradictions: int, kept: int}``.
    """
    cutoff = datetime.now(UTC) - timedelta(days=7)

    all_records = repository.list_all(user_id=user_id)
    old_records = [
        r for r in all_records
        if _parse_ts(r.timestamp) < cutoff
    ]

    if not old_records:
        return {"archived": 0, "contradictions": 0, "kept": len(all_records)}

    # Group by (asset, strategy_id)
    def _group_key(r: MemoryRecord) -> tuple[str, str]:
        return (r.asset, r.strategy_id or "")

    old_records.sort(key=_group_key)

    archived = 0
    contradictions = 0
    kept_ids: set[str] = set()

    for _key, group_iter in groupby(old_records, key=_group_key):
        group = list(group_iter)

        # Sort: prefer higher outcome_sharpe, fallback to most recent
        group.sort(
            key=lambda r: (
                r.outcome_sharpe if r.outcome_sharpe is not None else -1e9,
                _parse_ts(r.timestamp).timestamp(),
            ),
            reverse=True,
        )

        top5 = group[:5]
        rest = group[5:]

        # Detect contradictions within the top-5
        contradictions += _resolve_contradictions(top5, repository, dry_run=dry_run)

        for r in top5:
            kept_ids.add(r.id)

        # Archive the rest
        for r in rest:
            if not dry_run:
                _mark_archived(r, repository)
            archived += 1

    kept = len(all_records) - archived
    return {"archived": archived, "contradictions": contradictions, "kept": kept}


def prune_old_records(user_id: str, repository, max_age_days: int = 90) -> int:
    """Delete records older than *max_age_days* that have no positive trade_outcome."""
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

    all_records = repository.list_all(user_id=user_id)
    deleted = 0
    for r in all_records:
        ts = _parse_ts(r.timestamp)
        if ts >= cutoff:
            continue
        # Keep records with a positive trade outcome
        if r.trade_outcome is not None and r.trade_outcome > 0:
            continue
        try:
            repository._store.execute(
                "DELETE FROM memory_records WHERE id = :id AND user_id = :user_id",
                {"id": r.id, "user_id": user_id},
            )
            repository._items.pop(r.id, None)
            deleted += 1
        except Exception as exc:
            logger.warning("prune_delete_failed", extra={"id": r.id, "error": str(exc)[:100]})

    return deleted


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts) -> datetime:
    """Normalise a timestamp (str or datetime) to a timezone-aware datetime."""
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts)
    else:
        dt = ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _mark_archived(record: MemoryRecord, repository) -> None:
    """Set ``archived: True`` in the record's metadata via DB update."""
    meta = dict(record.metadata)
    meta["archived"] = True
    try:
        from shared.persistence import serialize_json
        repository._store.execute(
            "UPDATE memory_records SET metadata = CAST(:meta AS JSONB) WHERE id = :id",
            {"meta": serialize_json(meta), "id": record.id},
        )
        if record.id in repository._items:
            repository._items[record.id].metadata = meta
    except Exception as exc:
        logger.warning("archive_update_failed", extra={"id": record.id, "error": str(exc)[:100]})


def _resolve_contradictions(
    records: list[MemoryRecord],
    repository,
    *,
    dry_run: bool = False,
) -> int:
    """Detect contradictions within a group: same asset, BUY vs SELL with
    similar signal_score (within 0.1) but different outcomes.
    Keep only the most recent of each contradicting pair.
    """
    contradictions = 0
    to_remove: set[str] = set()

    for i, a in enumerate(records):
        if a.id in to_remove:
            continue
        for b in records[i + 1:]:
            if b.id in to_remove:
                continue
            if not _is_contradiction(a, b):
                continue
            contradictions += 1
            logger.info(
                "memory_contradiction_detected",
                extra={
                    "asset": a.asset,
                    "action_a": a.action,
                    "action_b": b.action,
                    "signal_a": a.signal_score,
                    "signal_b": b.signal_score,
                },
            )
            # Keep the more recent one, archive the older
            older = b if _parse_ts(a.timestamp) >= _parse_ts(b.timestamp) else a
            to_remove.add(older.id)
            if not dry_run:
                _mark_archived(older, repository)

    return contradictions


def _is_contradiction(a: MemoryRecord, b: MemoryRecord) -> bool:
    """Two records contradict if they target the same asset with opposing
    actions (BUY/SELL), similar signal_score (within 0.1), and different
    trade outcomes (one positive, the other non-positive or vice-versa).
    """
    if a.asset != b.asset:
        return False
    actions = {a.action, b.action}
    if actions != {"BUY", "SELL"}:
        return False
    if abs(a.signal_score - b.signal_score) > 0.1:
        return False
    # Different outcome signs (or one missing)
    out_a = a.trade_outcome if a.trade_outcome is not None else 0.0
    out_b = b.trade_outcome if b.trade_outcome is not None else 0.0
    if (out_a > 0) == (out_b > 0) and out_a != 0.0 and out_b != 0.0:
        return False  # same sign — not a contradiction
    return True
