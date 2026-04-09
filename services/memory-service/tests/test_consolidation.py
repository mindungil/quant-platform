"""Tests for DREAM-style memory consolidation."""
from datetime import datetime, timedelta, timezone

from app.core.consolidation import consolidate_memories, prune_old_records
from app.models.memory import MemoryRecord

UTC = timezone.utc


class FakeStore:
    """Minimal stub for SqlStore used by the repository."""

    def __init__(self):
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql: str, params: dict | None = None):
        self.executed.append((sql, params or {}))


class FakeRepository:
    """In-memory repository stub for consolidation tests."""

    def __init__(self, records: list[MemoryRecord]):
        self._items: dict[str, MemoryRecord] = {r.id: r for r in records}
        self._store = FakeStore()

    def list_all(self, user_id: str | None = None) -> list[MemoryRecord]:
        if user_id is None:
            return list(self._items.values())
        return [r for r in self._items.values() if r.user_id == user_id]


def _make_record(
    asset: str = "BTCUSDT",
    action: str = "BUY",
    signal_score: float = 0.8,
    strategy_id: str = "strat1",
    days_old: int = 10,
    outcome_sharpe: float | None = None,
    trade_outcome: float | None = None,
    user_id: str = "user1",
) -> MemoryRecord:
    ts = datetime.now(UTC) - timedelta(days=days_old)
    return MemoryRecord(
        user_id=user_id,
        asset=asset,
        asset_type="crypto",
        signal_score=signal_score,
        action=action,
        strategy_id=strategy_id,
        reasoning="test",
        timestamp=ts.isoformat(),
        outcome_sharpe=outcome_sharpe,
        trade_outcome=trade_outcome,
    )


def test_consolidate_archives_beyond_top5():
    # Create 8 records in same group, all older than 7 days
    records = [
        _make_record(outcome_sharpe=float(i), days_old=10)
        for i in range(8)
    ]
    repo = FakeRepository(records)
    result = consolidate_memories("user1", repo)

    assert result["archived"] == 3  # 8 - 5
    assert result["contradictions"] == 0
    # archived records got metadata update via store.execute
    assert len(repo._store.executed) == 3


def test_consolidate_detects_contradiction():
    records = [
        _make_record(action="BUY", signal_score=0.80, trade_outcome=0.05, outcome_sharpe=1.0, days_old=10),
        _make_record(action="SELL", signal_score=0.85, trade_outcome=-0.03, outcome_sharpe=-0.5, days_old=12),
    ]
    repo = FakeRepository(records)
    result = consolidate_memories("user1", repo)

    assert result["contradictions"] == 1
    # The older contradiction is archived via _mark_archived (metadata update)
    assert len(repo._store.executed) >= 1


def test_consolidate_dry_run_does_not_mutate():
    records = [
        _make_record(outcome_sharpe=float(i), days_old=10)
        for i in range(8)
    ]
    repo = FakeRepository(records)
    result = consolidate_memories("user1", repo, dry_run=True)

    assert result["archived"] == 3
    assert len(repo._store.executed) == 0  # no DB mutations in dry-run


def test_consolidate_skips_recent_records():
    records = [_make_record(days_old=3)]  # only 3 days old
    repo = FakeRepository(records)
    result = consolidate_memories("user1", repo)

    assert result["archived"] == 0
    assert result["kept"] == 1


def test_prune_deletes_old_negative_outcome():
    records = [
        _make_record(days_old=100, trade_outcome=-0.05),
        _make_record(days_old=100, trade_outcome=0.10),  # positive — should survive
        _make_record(days_old=50),  # within 90 days
    ]
    repo = FakeRepository(records)
    deleted = prune_old_records("user1", repo, max_age_days=90)

    assert deleted == 1  # only the negative old one
    assert len(repo._store.executed) == 1
