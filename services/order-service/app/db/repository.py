import os
from app.models.order import OrderResponse
from shared.persistence import SqlStore, deserialize_json, serialize_json


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

    def _hydrate(self, row: dict) -> OrderResponse:
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
            }
        )

    def save(self, user_id: str, response: OrderResponse) -> None:
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


order_repository = OrderRepository()
