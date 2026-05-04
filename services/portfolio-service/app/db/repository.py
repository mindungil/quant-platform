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


def _next_position_state(current_qty: float, current_avg: float, side: str, fill_qty: float, fill_price: float) -> tuple[float, float]:
    signed_qty = fill_qty if side == "BUY" else -fill_qty
    new_qty = round(current_qty + signed_qty, 8)

    if abs(new_qty) < 1e-8:
        return 0.0, 0.0

    if current_qty == 0:
        return new_qty, float(fill_price)

    same_direction = (current_qty > 0 and signed_qty > 0) or (current_qty < 0 and signed_qty < 0)
    if same_direction:
        total_qty = abs(current_qty) + abs(fill_qty)
        avg = ((abs(current_qty) * current_avg) + (abs(fill_qty) * fill_price)) / total_qty if total_qty > 0 else float(fill_price)
        return new_qty, round(avg, 8)

    flipped = (current_qty > 0 > new_qty) or (current_qty < 0 < new_qty)
    if flipped:
        return new_qty, float(fill_price)

    return new_qty, float(current_avg or fill_price)


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
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                positions JSONB NOT NULL DEFAULT '{}'::jsonb,
                average_entry_prices JSONB NOT NULL DEFAULT '{}'::jsonb,
                total_exposure DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                total_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                realized_pnl_total DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                daily_return_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def apply(self, payload: PositionUpdate) -> PortfolioSnapshot:
        self._items.setdefault(payload.user_id, {})
        self._prices.setdefault(payload.user_id, {})
        self._fills.setdefault(payload.user_id, [])

        current = self._items[payload.user_id].get(payload.asset, 0.0)
        current_avg = self._prices[payload.user_id].get(payload.asset, 0.0)
        new_quantity, average_entry_price = _next_position_state(
            float(current),
            float(current_avg),
            payload.side,
            float(payload.quantity),
            float(payload.price),
        )
        self._items[payload.user_id][payload.asset] = new_quantity
        if new_quantity == 0.0:
            self._items[payload.user_id].pop(payload.asset, None)
            self._prices[payload.user_id].pop(payload.asset, None)
        else:
            self._prices[payload.user_id][payload.asset] = average_entry_price
        self._fills[payload.user_id].append(payload)
        portfolio_fills_total.labels(side=payload.side).inc()

        self._store.execute(
            """
            INSERT INTO portfolio_fills (user_id, order_id, asset, side, quantity, price, notional)
            VALUES (:user_id, :order_id, :asset, :side, :quantity, :price, :notional)
            """,
            payload.model_dump(mode="json"),
            scope_user_id=payload.user_id,
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
                "average_entry_price": average_entry_price,
            },
            scope_user_id=payload.user_id,
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
            scope_user_id=user_id,
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
            scope_user_id=user_id,
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
                scope_user_id=user_id,
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


    def save_snapshot(self, user_id: str, snapshot: PortfolioSnapshot) -> None:
        """Persist a portfolio snapshot with timestamp."""
        from shared.persistence import serialize_json
        # Calculate cumulative realized PnL from all closed trades
        realized_pnl_total = self._compute_realized_pnl_total(user_id)

        self._store.execute(
            """
            INSERT INTO portfolio_snapshots
                (user_id, positions, average_entry_prices, total_exposure,
                 unrealized_pnl, realized_pnl, total_pnl, realized_pnl_total, daily_return_pct)
            VALUES
                (:user_id, CAST(:positions AS JSONB), CAST(:prices AS JSONB), :total_exposure,
                 :unrealized_pnl, :realized_pnl, :total_pnl, :realized_pnl_total, :daily_return_pct)
            """,
            {
                "user_id": user_id,
                "positions": serialize_json(snapshot.positions),
                "prices": serialize_json(snapshot.average_entry_prices),
                "total_exposure": snapshot.total_exposure,
                "unrealized_pnl": snapshot.unrealized_pnl,
                "realized_pnl": snapshot.realized_pnl,
                "total_pnl": snapshot.total_pnl,
                "realized_pnl_total": realized_pnl_total,
                "daily_return_pct": snapshot.daily_return_pct,
            },
        )

    def get_snapshot_history(self, user_id: str, limit: int = 30) -> list[dict]:
        """Return list of snapshots with timestamps."""
        rows = self._store.fetch_all(
            """
            SELECT user_id, positions, average_entry_prices, total_exposure,
                   unrealized_pnl, realized_pnl, total_pnl, realized_pnl_total,
                   daily_return_pct, created_at
            FROM portfolio_snapshots
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
            scope_user_id=user_id,
        )
        from shared.persistence import deserialize_json
        return [
            {
                "user_id": row["user_id"],
                "positions": deserialize_json(row["positions"]) if isinstance(row["positions"], str) else row["positions"],
                "average_entry_prices": deserialize_json(row["average_entry_prices"]) if isinstance(row["average_entry_prices"], str) else row["average_entry_prices"],
                "total_exposure": row["total_exposure"],
                "unrealized_pnl": row["unrealized_pnl"],
                "realized_pnl": row["realized_pnl"],
                "total_pnl": row["total_pnl"],
                "realized_pnl_total": row["realized_pnl_total"],
                "daily_return_pct": row["daily_return_pct"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _compute_realized_pnl_total(self, user_id: str) -> float:
        """Cumulative sum of all closed trade PnLs."""
        # Match sells against buys for realized PnL
        rows = self._store.fetch_all(
            """
            SELECT asset, side, quantity, price
            FROM portfolio_fills
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            """,
            {"user_id": user_id},
            scope_user_id=user_id,
        )
        # Track cost basis per asset
        cost_basis: dict[str, list[tuple[float, float]]] = {}  # asset -> [(qty, price)]
        realized = 0.0

        for row in rows:
            asset = row["asset"]
            side = row["side"]
            qty = row["quantity"]
            price = row["price"]
            cost_basis.setdefault(asset, [])

            if side == "BUY":
                cost_basis[asset].append((qty, price))
            elif side == "SELL":
                remaining = qty
                while remaining > 0 and cost_basis[asset]:
                    entry_qty, entry_price = cost_basis[asset][0]
                    match_qty = min(remaining, entry_qty)
                    realized += match_qty * (price - entry_price)
                    remaining -= match_qty
                    if match_qty >= entry_qty:
                        cost_basis[asset].pop(0)
                    else:
                        cost_basis[asset][0] = (entry_qty - match_qty, entry_price)

        return round(realized, 4)

    def get_portfolio_with_live_pnl(self, user_id: str) -> dict:
        """Get portfolio with real-time unrealized PnL from live market prices."""
        positions = self.get_positions(user_id)

        for pos in positions:
            asset = pos.get("asset", "")
            entry_price = pos.get("avg_entry", 0)
            quantity = pos.get("quantity", 0)

            # Fetch live price from market-data
            try:
                market_url = os.environ.get("MARKET_DATA_BASE_URL", "http://localhost:8001")
                resp = httpx.get(f"{market_url}/candles/{asset}/latest", timeout=3.0)
                if resp.status_code == 200:
                    live_price = resp.json().get("close", entry_price)
                    pos["current_price"] = live_price
                    pos["unrealized_pnl"] = (live_price - entry_price) * quantity
                    pos["unrealized_pnl_pct"] = ((live_price / entry_price) - 1) * 100 if entry_price > 0 else 0
            except Exception:
                pos.setdefault("current_price", entry_price)
                pos.setdefault("unrealized_pnl", 0)
                pos["unrealized_pnl_pct"] = 0

        total_value = sum(p.get("current_price", 0) * p.get("quantity", 0) for p in positions)
        total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

        return {
            "user_id": user_id,
            "positions": positions,
            "total_value": round(total_value, 4),
            "total_unrealized_pnl": round(total_pnl, 4),
            "total_unrealized_pnl_pct": round((total_pnl / total_value * 100), 4) if total_value > 0 else 0,
        }

    def get_aggregate(self) -> dict:
        """Aggregate positions across all users for internal risk monitoring."""
        rows = self._store.fetch_all(
            """
            SELECT asset, SUM(ABS(quantity)) AS total_qty, SUM(quantity) AS net_qty
            FROM portfolio_positions
            GROUP BY asset
            ORDER BY total_qty DESC
            """,
            scope_user_id=None,
        )
        if not rows:
            return {
                "total_exposure": 0.0,
                "concentration": {},
                "largest_position": "",
                "rebalance_needed": False,
            }

        assets = [row["asset"] for row in rows]
        current_prices = _fetch_current_prices(assets)

        total_exposure = 0.0
        concentration: dict[str, float] = {}
        largest_position = ""
        largest_value = 0.0

        for row in rows:
            asset = row["asset"]
            total_qty = float(row["total_qty"])
            price = current_prices.get(asset, 0.0)
            value = total_qty * price
            total_exposure += value
            concentration[asset] = value
            if value > largest_value:
                largest_value = value
                largest_position = asset

        # Normalize concentration to weights
        if total_exposure > 0:
            concentration = {k: round(v / total_exposure, 4) for k, v in concentration.items()}

        total_exposure = round(total_exposure, 4)
        max_weight = max(concentration.values()) if concentration else 0.0
        rebalance_needed = total_exposure > 100000 or max_weight > 0.30

        return {
            "total_exposure": total_exposure,
            "concentration": concentration,
            "largest_position": largest_position,
            "rebalance_needed": rebalance_needed,
        }

    def get_summary(self) -> dict:
        """Compliance-shaped view: equity, signed notional positions, kill_switch.

        Consumed by signal-service's PortfolioStateProvider to judge pre-trade
        compliance. Equity is total gross exposure across all users (internal
        risk view). Positions are signed notional per asset. kill_switch is
        read from Redis key 'kill_switch:global' with default False.
        """
        rows = self._store.fetch_all(
            """
            SELECT asset, SUM(quantity) AS net_qty
            FROM portfolio_positions
            GROUP BY asset
            """,
            scope_user_id=None,
        )
        assets = [row["asset"] for row in rows] if rows else []
        prices = _fetch_current_prices(assets) if assets else {}

        positions: dict[str, float] = {}
        gross = 0.0
        for row in rows or []:
            asset = row["asset"]
            net_qty = float(row["net_qty"] or 0.0)
            px = float(prices.get(asset, 0.0))
            notional = net_qty * px
            positions[asset] = round(notional, 4)
            gross += abs(notional)

        kill = False
        try:
            raw = RedisStore(settings.redis_url).get("kill_switch:global")
            if raw is not None:
                kill = str(raw).lower() in ("1", "true", "yes")
        except Exception:
            pass

        return {
            "equity": round(gross, 4),
            "positions": positions,
            "kill_switch": kill,
        }

    def get_positions(self, user_id: str) -> list[dict]:
        """Return per-asset net positions with side, qty, avg_entry, unrealized PnL."""
        fills = self._store.fetch_all(
            """
            SELECT asset, side, quantity, price
            FROM portfolio_fills
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            """,
            {"user_id": user_id},
            scope_user_id=user_id,
        )

        # Compute net quantity per asset
        net: dict[str, float] = {}
        cost_basis: dict[str, float] = {}  # weighted avg entry
        total_cost: dict[str, float] = {}

        for row in fills:
            asset = row["asset"]
            qty = row["quantity"]
            price = row["price"]
            signed = qty if row["side"] == "BUY" else -qty

            prev_net = net.get(asset, 0.0)
            new_net = prev_net + signed

            # Update cost basis for entries
            if row["side"] == "BUY" and new_net > 0:
                total_cost[asset] = total_cost.get(asset, 0.0) + qty * price
                if abs(new_net) > 0:
                    cost_basis[asset] = total_cost.get(asset, 0.0) / max(abs(new_net), 1e-12)
            elif row["side"] == "SELL" and abs(new_net) < abs(prev_net):
                # Closing position — reduce cost basis proportionally
                if abs(prev_net) > 0:
                    ratio = abs(new_net) / abs(prev_net)
                    total_cost[asset] = total_cost.get(asset, 0.0) * ratio

            net[asset] = round(new_net, 8)

        # Fetch current prices
        assets_with_positions = [a for a, q in net.items() if abs(q) > 1e-12]
        current_prices = _fetch_current_prices(assets_with_positions)

        result = []
        for asset, quantity in net.items():
            if abs(quantity) < 1e-12:
                side = "FLAT"
            elif quantity > 0:
                side = "LONG"
            else:
                side = "SHORT"

            avg_entry = cost_basis.get(asset, 0.0)
            current_price = current_prices.get(asset, avg_entry)
            unrealized_pnl = round((current_price - avg_entry) * quantity, 4) if avg_entry > 0 else 0.0

            result.append({
                "asset": asset,
                "quantity": quantity,
                "avg_entry": round(avg_entry, 4),
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "side": side,
            })

        return result


portfolio_repository = PortfolioRepository()
