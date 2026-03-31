import os

from app.models.portfolio import PortfolioSnapshot, PositionUpdate
from shared.persistence import RedisStore, SqlStore
from shared.realtime import RealtimeBus


class PortfolioRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, float]] = {}
        self._prices: dict[str, dict[str, float]] = {}
        self._fills: dict[str, list[PositionUpdate]] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._realtime = RealtimeBus(RedisStore(os.getenv("REDIS_URL", "redis://localhost:6379/0")))
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
        return snapshot

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

        total_exposure = round(sum(abs(quantity) * prices.get(asset, 0.0) for asset, quantity in positions.items()), 4)
        return PortfolioSnapshot(
            user_id=user_id,
            positions=positions,
            average_entry_prices=prices,
            recent_fills=recent_fills,
            total_exposure=total_exposure,
            rebalance_needed=total_exposure > 100000,
            updated_at=updated_at,
        )


portfolio_repository = PortfolioRepository()
