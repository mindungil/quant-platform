import os

import httpx
from prometheus_client import Counter

from app.core.config import settings
from app.models.portfolio import PortfolioSnapshot, PositionUpdate
from shared.asyncio_utils import run_coro
from shared.events import EventEnvelope, JetStreamBus
from shared.logging import get_logger
from shared.persistence import RedisStore, SqlStore
from shared.realtime import RealtimeBus

logger = get_logger("portfolio-service")


def _fetch_current_prices(assets: list[str]) -> dict[str, float]:
    """Fetch current prices from market-data service."""
    market_data_url = os.getenv("MARKET_DATA_BASE_URL", "http://localhost:8001")
    prices = {}
    for asset in assets:
        try:
            resp = httpx.get(f"{market_data_url}/candles/{asset}/latest", timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                price = data.get("close") or data.get("price")
                if price:
                    prices[asset] = float(price)
        except Exception:
            pass
    return prices

portfolio_fills_total = Counter(
    "portfolio_fills_total",
    "Total portfolio fills recorded",
    ["side"],
)


class PortfolioRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, float]] = {}
        self._prices: dict[str, dict[str, float]] = {}
        self._fills: dict[str, list[PositionUpdate]] = {}
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
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                user_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                average_entry_price DOUBLE PRECISION NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, asset)
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_fills (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                order_id TEXT,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                notional DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def apply(self, payload: PositionUpdate) -> PortfolioSnapshot:
        self._items.setdefault(payload.user_id, {})
        self._prices.setdefault(payload.user_id, {})
        self._fills.setdefault(payload.user_id, [])

        current = self._items[payload.user_id].get(payload.asset, 0.0)
        signed_quantity = payload.quantity if payload.side == "BUY" else -payload.quantity
        new_quantity = round(current + signed_quantity, 8)
        self._items[payload.user_id][payload.asset] = new_quantity
        if payload.side == "BUY" and payload.price > 0:
            self._prices[payload.user_id][payload.asset] = payload.price
        self._fills[payload.user_id].append(payload)
        portfolio_fills_total.labels(side=payload.side).inc()

        self._store.execute(
            """
            INSERT INTO portfolio_fills (user_id, order_id, asset, side, quantity, price, notional)
            VALUES (:user_id, :order_id, :asset, :side, :quantity, :price, :notional)
            """,
            payload.model_dump(mode="json"),
        )
        self._store.execute(
            """
            INSERT INTO portfolio_positions (user_id, asset, quantity, average_entry_price, updated_at)
            VALUES (:user_id, :asset, :quantity, :average_entry_price, NOW())
            ON CONFLICT (user_id, asset) DO UPDATE SET
                quantity = EXCLUDED.quantity,
                average_entry_price = EXCLUDED.average_entry_price,
                updated_at = NOW()
            """,
            {
                "user_id": payload.user_id,
                "asset": payload.asset,
                "quantity": new_quantity,
                "average_entry_price": self._prices[payload.user_id].get(payload.asset, payload.price),
            },
        )
        snapshot = self.get(payload.user_id)
        self._realtime.publish(
            event_type="portfolio.updated",
            source="portfolio-service",
            user_id=payload.user_id,
            data=snapshot.model_dump(mode="json"),
        )
        run_coro(
            self._publish_portfolio_event(
                user_id=payload.user_id,
                correlation_id=payload.correlation_id or payload.order_id,
                snapshot=snapshot,
            )
        )
        return snapshot

    async def _publish_portfolio_event(
        self,
        *,
        user_id: str,
        correlation_id: str | None,
        snapshot: PortfolioSnapshot,
    ) -> None:
        await self._bus.connect()
        await self._bus.ensure_stream(settings.execution_jetstream_stream, ["portfolio.updated", "portfolio.updated.dlq"])
        await self._bus.publish(
            "portfolio.updated",
            EventEnvelope(
                event_type="portfolio.updated",
                source="portfolio-service",
                correlation_id=correlation_id,
                user_id=user_id,
                data=snapshot.model_dump(mode="json"),
            ),
        )
        logger.info(
            "portfolio_updated",
            extra={
                "service": "portfolio-service",
                "correlation_id": correlation_id,
                "user_id": user_id,
                "event_type": "portfolio.updated",
            },
        )

    def get(self, user_id: str) -> PortfolioSnapshot:
        position_rows = self._store.fetch_all(
            """
            SELECT asset, quantity, average_entry_price, updated_at
            FROM portfolio_positions
            WHERE user_id = :user_id
            ORDER BY asset ASC
            """,
            {"user_id": user_id},
        )
        fill_rows = self._store.fetch_all(
            """
            SELECT user_id, asset, side, quantity, price, notional, order_id
            FROM portfolio_fills
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 10
            """,
            {"user_id": user_id},
        )

        if position_rows:
            positions = {row["asset"]: row["quantity"] for row in position_rows}
            prices = {row["asset"]: row["average_entry_price"] for row in position_rows}
            recent_fills = [PositionUpdate.model_validate(row) for row in reversed(fill_rows)]
            updated_at = max(row["updated_at"] for row in position_rows)
        else:
            positions = self._items.get(user_id, {})
            prices = self._prices.get(user_id, {})
            recent_fills = self._fills.get(user_id, [])[-10:]
            updated_at = None

        # Fetch live market prices for unrealized PnL
        current_prices = _fetch_current_prices(list(positions.keys()))

        total_exposure = round(sum(abs(quantity) * current_prices.get(asset, prices.get(asset, 0.0)) for asset, quantity in positions.items()), 4)

        # Unrealized P&L per position
        unrealized_pnl = 0.0
        concentration: dict[str, float] = {}
        largest_position = ""
        largest_weight = 0.0

        for asset, quantity in positions.items():
            entry_price = prices.get(asset, 0.0)
            current_price = current_prices.get(asset, entry_price)
            unrealized_pnl += (current_price - entry_price) * quantity
            position_value = abs(quantity) * current_price
            if total_exposure > 0:
                weight = round(position_value / total_exposure, 4)
                concentration[asset] = weight
                if weight > largest_weight:
                    largest_weight = weight
                    largest_position = asset

        unrealized_pnl = round(unrealized_pnl, 4)

        # Realized PnL from recent fills
        realized_pnl = 0.0
        for fill in recent_fills:
            fill_pnl = getattr(fill, "pnl", None)
            if fill_pnl is not None:
                realized_pnl += fill_pnl
        realized_pnl = round(realized_pnl, 4)

        total_pnl = round(unrealized_pnl + realized_pnl, 4)

        # Daily return %: compare current total to previous snapshot
        daily_return_pct = 0.0
        current_total = total_exposure + unrealized_pnl
        if user_id and updated_at:
            prev_row = self._store.fetch_one(
                """
                SELECT total_exposure, unrealized_pnl
                FROM portfolio_snapshots
                WHERE user_id = :user_id AND created_at < :before
                ORDER BY created_at DESC LIMIT 1
                """,
                {"user_id": user_id, "before": updated_at},
            )
            if prev_row:
                previous_total = (prev_row.get("total_exposure", 0) or 0) + (prev_row.get("unrealized_pnl", 0) or 0)
                if previous_total > 0:
                    daily_return_pct = round((current_total - previous_total) / previous_total, 6)

        # Concentration-based rebalance check
        max_weight = 0.30  # 30% max single asset
        rebalance_needed = total_exposure > 100000 or largest_weight > max_weight

        return PortfolioSnapshot(
            user_id=user_id,
            positions=positions,
            average_entry_prices=prices,
            recent_fills=recent_fills,
            total_exposure=total_exposure,
            unrealized_pnl=unrealized_pnl,
            realized_pnl=realized_pnl,
            total_pnl=total_pnl,
            daily_return_pct=daily_return_pct,
            concentration=concentration,
            largest_position=largest_position,
            rebalance_needed=rebalance_needed,
            updated_at=updated_at,
        )


portfolio_repository = PortfolioRepository()
