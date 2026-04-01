import os
from datetime import UTC, datetime

from app.models.order import ExecutionConfig, OrderResponse
from shared.persistence import SqlStore, deserialize_json, serialize_json
from shared.runtime import runtime_flags


class OrderRepository:
    def __init__(self) -> None:
        self._orders: dict[str, list[OrderResponse]] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS order_events (
                order_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL,
                risk_reason TEXT NOT NULL,
                exchange_name TEXT NOT NULL,
                shadow_mode BOOLEAN NOT NULL,
                credential JSONB NOT NULL,
                fill JSONB,
                portfolio JSONB,
                statistics JSONB
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS order_lifecycle_events (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL,
                detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_config (
                scope TEXT PRIMARY KEY,
                live_trading_enabled BOOLEAN NOT NULL,
                allowed_exchanges JSONB NOT NULL,
                default_shadow_mode BOOLEAN NOT NULL,
                strict_runtime BOOLEAN NOT NULL,
                updated_by TEXT,
                updated_at TIMESTAMPTZ
            )
            """
        )
        defaults = runtime_flags()
        self._store.execute(
            """
            INSERT INTO execution_config (
                scope, live_trading_enabled, allowed_exchanges, default_shadow_mode, strict_runtime, updated_by, updated_at
            ) VALUES (
                'global', :live_trading_enabled, CAST(:allowed_exchanges AS JSONB), :default_shadow_mode, :strict_runtime, NULL, NOW()
            )
            ON CONFLICT (scope) DO NOTHING
            """,
            {
                "live_trading_enabled": defaults.live_trading_enabled,
                "allowed_exchanges": serialize_json(list(defaults.allowed_live_exchanges)),
                "default_shadow_mode": defaults.default_shadow_mode,
                "strict_runtime": defaults.strict_runtime,
            },
        )

    def _hydrate(self, row: dict) -> OrderResponse:
        lifecycle_rows = self._store.fetch_all(
            """
            SELECT status, detail, created_at
            FROM order_lifecycle_events
            WHERE order_id = :order_id
            ORDER BY created_at ASC, id ASC
            """,
            {"order_id": row["order_id"]},
        )
        return OrderResponse.model_validate(
            {
                "user_id": row["user_id"],
                "order_id": row["order_id"],
                "created_at": row["created_at"],
                "asset": row["asset"],
                "side": row["side"],
                "quantity": row["quantity"],
                "status": row["status"],
                "risk_reason": row["risk_reason"],
                "exchange": row["exchange_name"],
                "shadow_mode": row["shadow_mode"],
                "credential": deserialize_json(row["credential"]) or {},
                "fill": deserialize_json(row["fill"]),
                "portfolio": deserialize_json(row["portfolio"]),
                "statistics": deserialize_json(row["statistics"]),
                "lifecycle": [
                    {
                        "status": item["status"],
                        "detail": deserialize_json(item["detail"]) or {},
                        "created_at": item["created_at"],
                    }
                    for item in lifecycle_rows
                ],
            }
        )

    def save(self, user_id: str, response: OrderResponse, *, detail: dict | None = None) -> None:
        self._orders.setdefault(user_id, []).append(response)
        if response.order_id is None:
            return
        self._store.execute(
            """
            INSERT INTO order_events (
                order_id, user_id, created_at, asset, side, quantity, status, risk_reason, exchange_name,
                shadow_mode, credential, fill, portfolio, statistics
            ) VALUES (
                :order_id, :user_id, :created_at, :asset, :side, :quantity, :status, :risk_reason, :exchange_name,
                :shadow_mode, CAST(:credential AS JSONB), CAST(:fill AS JSONB), CAST(:portfolio AS JSONB), CAST(:statistics AS JSONB)
            )
            ON CONFLICT (order_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                created_at = EXCLUDED.created_at,
                asset = EXCLUDED.asset,
                side = EXCLUDED.side,
                quantity = EXCLUDED.quantity,
                status = EXCLUDED.status,
                risk_reason = EXCLUDED.risk_reason,
                exchange_name = EXCLUDED.exchange_name,
                shadow_mode = EXCLUDED.shadow_mode,
                credential = EXCLUDED.credential,
                fill = EXCLUDED.fill,
                portfolio = EXCLUDED.portfolio,
                statistics = EXCLUDED.statistics
            """,
            {
                "order_id": response.order_id,
                "user_id": user_id,
                "created_at": response.created_at,
                "asset": response.asset,
                "side": response.side,
                "quantity": response.quantity,
                "status": response.status,
                "risk_reason": response.risk_reason,
                "exchange_name": response.exchange,
                "shadow_mode": response.shadow_mode,
                "credential": serialize_json(response.credential.model_dump(mode="json")),
                "fill": serialize_json(response.fill.model_dump(mode="json")) if response.fill is not None else None,
                "portfolio": serialize_json(response.portfolio.model_dump(mode="json")) if response.portfolio is not None else None,
                "statistics": serialize_json(response.statistics.model_dump(mode="json")) if response.statistics is not None else None,
            },
        )
        self.record_lifecycle(response.order_id, user_id, response.status, detail=detail or {})

    def record_lifecycle(self, order_id: str, user_id: str, status: str, *, detail: dict) -> None:
        self._store.execute(
            """
            INSERT INTO order_lifecycle_events (order_id, user_id, status, detail, created_at)
            VALUES (:order_id, :user_id, :status, CAST(:detail AS JSONB), :created_at)
            """,
            {
                "order_id": order_id,
                "user_id": user_id,
                "status": status,
                "detail": serialize_json(detail),
                "created_at": datetime.now(UTC),
            },
        )

    def get_by_id(self, order_id: str) -> OrderResponse | None:
        row = self._store.fetch_one(
            "SELECT * FROM order_events WHERE order_id = :order_id",
            {"order_id": order_id},
        )
        if row is not None:
            return self._hydrate(row)
        for orders in self._orders.values():
            for order in orders:
                if order.order_id == order_id:
                    return order
        return None

    def update_status(self, order_id: str, status: str) -> None:
        self._store.execute(
            "UPDATE order_events SET status = :status WHERE order_id = :order_id",
            {"order_id": order_id, "status": status},
        )
        for orders in self._orders.values():
            for order in orders:
                if order.order_id == order_id:
                    order.status = status

    def list_for_user(self, user_id: str) -> list[OrderResponse]:
        rows = self._store.fetch_all(
            """
            SELECT * FROM order_events
            WHERE user_id = :user_id
            ORDER BY created_at ASC
            """,
            {"user_id": user_id},
        )
        if rows:
            return [self._hydrate(row) for row in rows]
        return self._orders.get(user_id, [])

    def get_execution_config(self) -> ExecutionConfig:
        row = self._store.fetch_one("SELECT * FROM execution_config WHERE scope = 'global'")
        if row is None:
            defaults = runtime_flags()
            return ExecutionConfig(
                live_trading_enabled=defaults.live_trading_enabled,
                allowed_exchanges=list(defaults.allowed_live_exchanges),
                default_shadow_mode=defaults.default_shadow_mode,
                strict_runtime=defaults.strict_runtime,
            )
        return ExecutionConfig(
            live_trading_enabled=bool(row["live_trading_enabled"]),
            allowed_exchanges=deserialize_json(row["allowed_exchanges"]) or ["binance"],
            default_shadow_mode=bool(row["default_shadow_mode"]),
            strict_runtime=bool(row["strict_runtime"]),
            updated_by=row.get("updated_by"),
            updated_at=row.get("updated_at"),
        )

    def update_execution_config(
        self,
        *,
        live_trading_enabled: bool,
        allowed_exchanges: list[str],
        default_shadow_mode: bool,
        strict_runtime: bool,
        updated_by: str,
    ) -> ExecutionConfig:
        updated_at = datetime.now(UTC)
        self._store.execute(
            """
            INSERT INTO execution_config (
                scope, live_trading_enabled, allowed_exchanges, default_shadow_mode, strict_runtime, updated_by, updated_at
            ) VALUES (
                'global', :live_trading_enabled, CAST(:allowed_exchanges AS JSONB), :default_shadow_mode, :strict_runtime, :updated_by, :updated_at
            )
            ON CONFLICT (scope) DO UPDATE SET
                live_trading_enabled = EXCLUDED.live_trading_enabled,
                allowed_exchanges = EXCLUDED.allowed_exchanges,
                default_shadow_mode = EXCLUDED.default_shadow_mode,
                strict_runtime = EXCLUDED.strict_runtime,
                updated_by = EXCLUDED.updated_by,
                updated_at = EXCLUDED.updated_at
            """,
            {
                "live_trading_enabled": live_trading_enabled,
                "allowed_exchanges": serialize_json([item.lower() for item in allowed_exchanges]),
                "default_shadow_mode": default_shadow_mode,
                "strict_runtime": strict_runtime,
                "updated_by": updated_by,
                "updated_at": updated_at,
            },
        )
        return self.get_execution_config()


order_repository = OrderRepository()
