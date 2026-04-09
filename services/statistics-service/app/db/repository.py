import os

from app.core.config import settings
from app.core.engine import compute_statistics
from app.models.statistics import StatisticsInput, StatisticsSnapshot
from shared.asyncio_utils import run_coro
from shared.events import EventEnvelope, JetStreamBus
from shared.logging import get_logger
from shared.persistence import RedisStore, SqlStore
from shared.realtime import RealtimeBus

logger = get_logger("statistics-service")


class StatisticsRepository:
    def __init__(self) -> None:
        self._trade_pnls: dict[str, list[float]] = {}
        self._expected_returns: dict[str, float] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._realtime = RealtimeBus(RedisStore(os.getenv("REDIS_URL", "redis://localhost:6379/0")))
        self._bus = JetStreamBus(
            nats_url=settings.nats_url,
            redis_store=RedisStore(settings.redis_url),
            enabled=settings.enable_nats,
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS statistics_trades (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                order_id TEXT,
                asset TEXT,
                pnl DOUBLE PRECISION NOT NULL,
                expected_return DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def record_trade(
        self,
        user_id: str,
        pnl: float,
        expected_return: float = 0.0,
        *,
        order_id: str | None = None,
        asset: str | None = None,
        correlation_id: str | None = None,
    ) -> StatisticsSnapshot:
        self._trade_pnls.setdefault(user_id, []).append(pnl)
        self._expected_returns[user_id] = expected_return
        self._store.execute(
            """
            INSERT INTO statistics_trades (user_id, order_id, asset, pnl, expected_return)
            VALUES (:user_id, :order_id, :asset, :pnl, :expected_return)
            """,
            {
                "user_id": user_id,
                "order_id": order_id,
                "asset": asset,
                "pnl": pnl,
                "expected_return": expected_return,
            },
        )
        snapshot = self.get(user_id)
        self._realtime.publish(
            event_type="statistics.updated",
            source="statistics-service",
            user_id=user_id,
            data=snapshot.model_dump(mode="json"),
        )
        run_coro(
            self._publish_statistics_event(
                user_id=user_id,
                correlation_id=correlation_id or order_id,
                snapshot=snapshot,
            )
        )
        return snapshot

    async def _publish_statistics_event(
        self,
        *,
        user_id: str,
        correlation_id: str | None,
        snapshot: StatisticsSnapshot,
    ) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(settings.execution_jetstream_stream, ["statistics.updated", "statistics.updated.dlq"])
        await self._bus.publish(
            "statistics.updated",
            EventEnvelope(
                event_type="statistics.updated",
                source="statistics-service",
                correlation_id=correlation_id,
                user_id=user_id,
                data=snapshot.model_dump(mode="json"),
            ),
        )
        logger.info(
            "statistics_updated",
            extra={
                "service": "statistics-service",
                "correlation_id": correlation_id,
                "user_id": user_id,
                "event_type": "statistics.updated",
            },
        )

    def get(self, user_id: str) -> StatisticsSnapshot:
        rows = self._store.fetch_all(
            """
            SELECT pnl, expected_return, created_at
            FROM statistics_trades
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            """,
            {"user_id": user_id},
        )
        if rows:
            snapshot = compute_statistics(
                StatisticsInput(
                    user_id=user_id,
                    trade_pnls=[row["pnl"] for row in rows],
                    expected_return=rows[-1]["expected_return"],
                )
            )
            snapshot.updated_at = rows[-1]["created_at"]
            return snapshot
        return compute_statistics(
            StatisticsInput(
                user_id=user_id,
                trade_pnls=self._trade_pnls.get(user_id, []),
                expected_return=self._expected_returns.get(user_id, 0.0),
            )
        )


    def get_trade_history(self, user_id: str, *, strategy_id: str | None = None, limit: int = 90) -> list[dict]:
        """Return raw trade PnL rows for equity curve generation."""
        rows = self._store.fetch_all(
            """
            SELECT pnl, expected_return, created_at, order_id, asset
            FROM statistics_trades
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [dict(row) for row in rows]

    def get_strategy_stats(self, user_id: str) -> list[dict]:
        """Compute per-strategy stats for comparison. Groups by asset as proxy for strategy."""
        rows = self._store.fetch_all(
            """
            SELECT asset, pnl
            FROM statistics_trades
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            """,
            {"user_id": user_id},
        )
        # Group by asset (used as strategy proxy)
        from collections import defaultdict
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            key = row.get("asset") or "unknown"
            groups[key].append(row["pnl"])

        import numpy as np
        result = []
        for strategy_id, pnls in groups.items():
            arr = np.array(pnls)
            trade_count = len(pnls)
            wins = arr[arr > 0]
            win_rate = round(float(len(wins) / trade_count), 4) if trade_count > 0 else 0.0
            avg_return = round(float(np.mean(arr)), 6) if trade_count > 0 else 0.0
            std = float(np.std(arr, ddof=1)) if trade_count > 1 else 0.0
            sharpe = round(float(np.mean(arr)) / std, 4) if std > 0 else 0.0

            result.append({
                "strategy_id": strategy_id,
                "sharpe": sharpe,
                "win_rate": win_rate,
                "trade_count": trade_count,
                "avg_return": avg_return,
                "total_return": round(float(np.sum(arr)), 4),
            })
        return result


statistics_repository = StatisticsRepository()
