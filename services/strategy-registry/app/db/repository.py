from datetime import UTC, datetime, timedelta

from app.models.strategy import (
    Strategy,
    StrategyCreate,
    VALID_STATUS_TRANSITIONS,
    SHADOW_DURATION_DAYS,
    SHADOW_MIN_TRADES,
    SHADOW_MIN_SHARPE,
)
import os
from shared.persistence import SqlStore, deserialize_json, serialize_json


class StrategyRepository:
    def __init__(self) -> None:
        self._items: dict[str, Strategy] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._ensure_schema()
        self._seed_default()

    @staticmethod
    def _table_names() -> tuple[str, str]:
        return ("strategy_records", "strategies")

    def _ensure_schema(self) -> None:
        schema = """
            CREATE TABLE IF NOT EXISTS {table_name} (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                indicators JSONB NOT NULL,
                weights JSONB NOT NULL,
                thresholds JSONB NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                backtest_results JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                shadow_metrics JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                shadow_start_at TIMESTAMPTZ
            )
        """
        for table_name in self._table_names():
            self._store.execute(schema.format(table_name=table_name))
        # Add updated_at column if missing (migration for existing tables)
        for table_name in self._table_names():
            self._store.execute(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )
        # Add shadow_start_at column if missing (migration for SHADOW lifecycle)
        for table_name in self._table_names():
            self._store.execute(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS shadow_start_at TIMESTAMPTZ"
            )
        self._store.execute(
            """
            INSERT INTO strategy_records (
                id, user_id, created_at, updated_at, name, asset_type, indicators, weights, thresholds, version, status, backtest_results, shadow_metrics, shadow_start_at
            )
            SELECT
                id, user_id, created_at, COALESCE(updated_at, created_at), name, asset_type, indicators, weights, thresholds, version, status, backtest_results, shadow_metrics, shadow_start_at
            FROM strategies
            ON CONFLICT (id) DO NOTHING
            """
        )

    def _seed_default(self) -> None:
        active_bootstrap = self._store.fetch_one(
            """
            SELECT * FROM strategy_records
            WHERE user_id = 'bootstrap' AND asset_type = 'crypto' AND status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
            """
        )
        if active_bootstrap is not None:
            return
        bootstrap_row = self._store.fetch_one(
            """
            SELECT * FROM strategy_records
            WHERE user_id = 'bootstrap' AND asset_type = 'crypto'
            ORDER BY created_at DESC LIMIT 1
            """
        )
        if bootstrap_row is not None:
            strategy = self._hydrate(bootstrap_row)
            strategy.status = "ACTIVE"
            self._items[strategy.id] = strategy
            self._persist(strategy)
            return
        strategy = Strategy(
            user_id="bootstrap",
            name="Crypto Momentum Bootstrap",
            asset_type="crypto",
            indicators=["rsi_14", "macd", "sma_20", "vwap"],
            weights={"rsi": 0.25, "macd": 0.25, "sma_20": 0.25, "vwap": 0.25},
            thresholds={"entry": 0.6, "exit": -0.6},
            version="v1",
            status="ACTIVE",
            backtest_results={"source": "bootstrap_seed"},
        )
        self._items[strategy.id] = strategy
        self._persist(strategy)

    def _persist(self, strategy: Strategy) -> None:
        values = {
            **strategy.model_dump(mode="json"),
            "indicators": serialize_json(strategy.indicators),
            "weights": serialize_json(strategy.weights),
            "thresholds": serialize_json(strategy.thresholds),
            "backtest_results": serialize_json(strategy.backtest_results),
            "shadow_metrics": serialize_json(strategy.shadow_metrics),
        }
        query = """
            INSERT INTO {table_name} (
                id, user_id, created_at, updated_at, name, asset_type, indicators, weights, thresholds, version, status, backtest_results, shadow_metrics, shadow_start_at
            ) VALUES (
                :id, :user_id, :created_at, :updated_at, :name, :asset_type, CAST(:indicators AS JSONB), CAST(:weights AS JSONB),
                CAST(:thresholds AS JSONB), :version, :status, CAST(:backtest_results AS JSONB), CAST(:shadow_metrics AS JSONB), :shadow_start_at
            )
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                name = EXCLUDED.name,
                asset_type = EXCLUDED.asset_type,
                indicators = EXCLUDED.indicators,
                weights = EXCLUDED.weights,
                thresholds = EXCLUDED.thresholds,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                backtest_results = EXCLUDED.backtest_results,
                shadow_metrics = EXCLUDED.shadow_metrics,
                shadow_start_at = EXCLUDED.shadow_start_at
        """
        for table_name in self._table_names():
            self._store.execute(query.format(table_name=table_name), values)

    def _hydrate(self, row: dict) -> Strategy:
        payload = dict(row)
        payload["indicators"] = deserialize_json(row["indicators"]) or []
        payload["weights"] = deserialize_json(row["weights"]) or {}
        payload["thresholds"] = deserialize_json(row["thresholds"]) or {}
        payload["backtest_results"] = deserialize_json(row["backtest_results"]) or {}
        payload["shadow_metrics"] = deserialize_json(row["shadow_metrics"]) or {}
        return Strategy(**payload)

    def _get_bootstrap_active(self, asset_type: str) -> Strategy | None:
        row = self._store.fetch_one(
            """
            SELECT * FROM strategies
            WHERE user_id = 'bootstrap' AND asset_type = :asset_type AND status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
            """,
            {"asset_type": asset_type},
        )
        if row is None:
            return None
        return self._hydrate(row)

    def create(self, payload: StrategyCreate) -> Strategy:
        strategy = Strategy(**payload.model_dump())
        self._items[strategy.id] = strategy
        self._persist(strategy)
        return strategy

    def get(self, strategy_id: str) -> Strategy | None:
        item = self._items.get(strategy_id)
        if item is not None:
            return item
        row = self._store.fetch_one("SELECT * FROM strategy_records WHERE id = :strategy_id", {"strategy_id": strategy_id})
        if row is None:
            row = self._store.fetch_one("SELECT * FROM strategies WHERE id = :strategy_id", {"strategy_id": strategy_id})
        if row is None:
            return None
        return self._hydrate(row)

    def get_active(self, asset_type: str) -> Strategy | None:
        active_items = [item for item in self._items.values() if item.asset_type == asset_type and item.status == "ACTIVE"]
        if active_items:
            return sorted(active_items, key=lambda item: item.created_at, reverse=True)[0]
        row = self._store.fetch_one(
            """
            SELECT * FROM strategy_records
            WHERE asset_type = :asset_type AND status = 'ACTIVE'
            ORDER BY CASE WHEN user_id = 'bootstrap' THEN 1 ELSE 0 END, created_at DESC
            LIMIT 1
            """,
            {"asset_type": asset_type},
        )
        if row is None:
            return None
        return self._hydrate(row)

    def get_active_for_user(self, asset_type: str, user_id: str) -> Strategy | None:
        row = self._store.fetch_one(
            """
            SELECT * FROM strategy_records
            WHERE user_id = :user_id AND asset_type = :asset_type AND status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
            """,
            {"user_id": user_id, "asset_type": asset_type},
        )
        if row is not None:
            return self._hydrate(row)
        return self._get_bootstrap_active(asset_type)

    _ALLOWED_ASSET_TYPES = {"crypto", "stock", "etf", "forex"}
    _ALLOWED_STATUSES = {"DRAFT", "PENDING", "TESTED", "SHADOW", "ACTIVE", "DEPRECATED", "ARCHIVED"}

    def list_strategies(
        self,
        asset_type: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
    ) -> list[Strategy]:
        # SQL-safe: only hardcoded condition strings, no user input interpolated
        conditions: list[str] = ["status != 'ARCHIVED'"]
        params: dict[str, str] = {}
        if asset_type is not None:
            if asset_type not in self._ALLOWED_ASSET_TYPES:
                return []
            conditions.append("asset_type = :asset_type")
            params["asset_type"] = asset_type
        if status is not None:
            if status not in self._ALLOWED_STATUSES:
                return []
            conditions.append("status = :status")
            params["status"] = status
        if user_id is not None:
            conditions.append("user_id = :user_id")
            params["user_id"] = user_id
        where = " AND ".join(conditions)
        rows = self._store.fetch_all(
            f"SELECT * FROM strategy_records WHERE {where} ORDER BY created_at DESC",
            params,
        )
        return [self._hydrate(row) for row in rows]

    def validate_transition(self, current_status: str, new_status: str) -> bool:
        allowed = VALID_STATUS_TRANSITIONS.get(current_status, set())
        return new_status in allowed

    def update_status(self, strategy_id: str, status: str) -> Strategy | None:
        strategy = self._items.get(strategy_id) or self.get(strategy_id)
        if strategy is None:
            return None
        if status == "ACTIVE":
            self._store.execute(
                """
                UPDATE strategy_records
                SET status = 'DEPRECATED'
                WHERE user_id = :user_id AND asset_type = :asset_type AND id != :strategy_id AND status = 'ACTIVE'
                """,
                {"user_id": strategy.user_id, "asset_type": strategy.asset_type, "strategy_id": strategy.id},
            )
            self._store.execute(
                """
                UPDATE strategies
                SET status = 'DEPRECATED'
                WHERE user_id = :user_id AND asset_type = :asset_type AND id != :strategy_id AND status = 'ACTIVE'
                """,
                {"user_id": strategy.user_id, "asset_type": strategy.asset_type, "strategy_id": strategy.id},
            )
            for item in self._items.values():
                if (
                    item.user_id == strategy.user_id
                    and item.asset_type == strategy.asset_type
                    and item.id != strategy.id
                    and item.status == "ACTIVE"
                ):
                    item.status = "DEPRECATED"
                    self._persist(item)
        # When entering SHADOW, record the start timestamp
        if status == "SHADOW":
            strategy.shadow_start_at = datetime.now(UTC)
            strategy.shadow_metrics = {
                "pnl": 0.0,
                "trade_count": 0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
            }
        strategy.status = status
        strategy.updated_at = datetime.now(UTC)
        self._items[strategy.id] = strategy
        self._persist(strategy)
        return strategy

    # ------------------------------------------------------------------
    # Shadow lifecycle methods
    # ------------------------------------------------------------------

    def get_shadow_strategies(self) -> list[Strategy]:
        """Return all strategies currently in SHADOW status."""
        rows = self._store.fetch_all(
            "SELECT * FROM strategy_records WHERE status = 'SHADOW' ORDER BY shadow_start_at ASC"
        )
        return [self._hydrate(row) for row in rows]

    def update_shadow_metrics(self, strategy_id: str, metrics: dict) -> Strategy | None:
        """Merge incoming shadow metrics into the strategy's shadow_metrics field."""
        strategy = self.get(strategy_id)
        if strategy is None or strategy.status != "SHADOW":
            return None
        existing = strategy.shadow_metrics or {}
        existing["pnl"] = existing.get("pnl", 0.0) + metrics.get("pnl", 0.0)
        existing["trade_count"] = existing.get("trade_count", 0) + metrics.get("trade_count", 0)
        # Overwrite point-in-time metrics (latest snapshot)
        for key in ("sharpe", "max_drawdown", "win_rate"):
            if key in metrics:
                existing[key] = metrics[key]
        strategy.shadow_metrics = existing
        strategy.updated_at = datetime.now(UTC)
        self._items[strategy.id] = strategy
        self._persist(strategy)
        return strategy

    def promote_shadow_if_ready(
        self,
        strategy_id: str,
        *,
        min_days: int = SHADOW_DURATION_DAYS,
        min_trades: int = SHADOW_MIN_TRADES,
        min_sharpe: float = SHADOW_MIN_SHARPE,
    ) -> tuple[str, Strategy | None]:
        """Check if a SHADOW strategy meets promotion criteria.

        Returns a tuple of (outcome, strategy) where outcome is one of:
        - "promoted"   — met all criteria, moved to ACTIVE
        - "deprecated" — shadow period ended but criteria not met, moved to DEPRECATED
        - "pending"    — still within shadow period
        - "not_found"  — strategy not found or not in SHADOW
        """
        strategy = self.get(strategy_id)
        if strategy is None or strategy.status != "SHADOW":
            return ("not_found", None)

        shadow_start = strategy.shadow_start_at
        if shadow_start is None:
            # Fallback: use updated_at as approximate shadow start
            shadow_start = strategy.updated_at

        if shadow_start.tzinfo is None:
            shadow_start = shadow_start.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        days_in_shadow = (now - shadow_start).total_seconds() / 86400.0

        if days_in_shadow < min_days:
            return ("pending", strategy)

        # Shadow period has elapsed — evaluate metrics
        metrics = strategy.shadow_metrics or {}
        trade_count = metrics.get("trade_count", 0)
        sharpe = metrics.get("sharpe", 0.0)

        if trade_count >= min_trades and sharpe >= min_sharpe:
            promoted = self.update_status(strategy_id, "ACTIVE")
            return ("promoted", promoted)
        else:
            deprecated = self.update_status(strategy_id, "DEPRECATED")
            return ("deprecated", deprecated)


strategy_repository = StrategyRepository()
