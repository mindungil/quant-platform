from app.models.strategy import Strategy, StrategyCreate
import os
from shared.persistence import SqlStore, deserialize_json, serialize_json


class StrategyRepository:
    def __init__(self) -> None:
        self._items: dict[str, Strategy] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._ensure_schema()
        self._seed_default()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                name TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                indicators JSONB NOT NULL,
                weights JSONB NOT NULL,
                thresholds JSONB NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                backtest_results JSONB NOT NULL DEFAULT '{}'::jsonb,
                shadow_metrics JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )

    def _seed_default(self) -> None:
        if self._store.fetch_one("SELECT id FROM strategies WHERE user_id = 'bootstrap' LIMIT 1") is not None:
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
        self._store.execute(
            """
            INSERT INTO strategies (
                id, user_id, created_at, name, asset_type, indicators, weights, thresholds, version, status, backtest_results, shadow_metrics
            ) VALUES (
                :id, :user_id, :created_at, :name, :asset_type, CAST(:indicators AS JSONB), CAST(:weights AS JSONB),
                CAST(:thresholds AS JSONB), :version, :status, CAST(:backtest_results AS JSONB), CAST(:shadow_metrics AS JSONB)
            )
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                created_at = EXCLUDED.created_at,
                name = EXCLUDED.name,
                asset_type = EXCLUDED.asset_type,
                indicators = EXCLUDED.indicators,
                weights = EXCLUDED.weights,
                thresholds = EXCLUDED.thresholds,
                version = EXCLUDED.version,
                status = EXCLUDED.status,
                backtest_results = EXCLUDED.backtest_results,
                shadow_metrics = EXCLUDED.shadow_metrics
            """,
            {
                **strategy.model_dump(mode="json"),
                "indicators": serialize_json(strategy.indicators),
                "weights": serialize_json(strategy.weights),
                "thresholds": serialize_json(strategy.thresholds),
                "backtest_results": serialize_json(strategy.backtest_results),
                "shadow_metrics": serialize_json(strategy.shadow_metrics),
            },
        )

    def _hydrate(self, row: dict) -> Strategy:
        return Strategy(
            **row,
            indicators=deserialize_json(row["indicators"]) or [],
            weights=deserialize_json(row["weights"]) or {},
            thresholds=deserialize_json(row["thresholds"]) or {},
            backtest_results=deserialize_json(row["backtest_results"]) or {},
            shadow_metrics=deserialize_json(row["shadow_metrics"]) or {},
        )

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
            SELECT * FROM strategies
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
            SELECT * FROM strategies
            WHERE user_id = :user_id AND asset_type = :asset_type AND status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
            """,
            {"user_id": user_id, "asset_type": asset_type},
        )
        if row is not None:
            return self._hydrate(row)
        return self._get_bootstrap_active(asset_type)

    def update_status(self, strategy_id: str, status: str) -> Strategy | None:
        strategy = self._items.get(strategy_id) or self.get(strategy_id)
        if strategy is None:
            return None
        if status == "ACTIVE":
            self._store.execute(
                """
                UPDATE strategies
                SET status = 'DEPRECATED'
                WHERE asset_type = :asset_type AND id != :strategy_id AND status = 'ACTIVE'
                """,
                {"asset_type": strategy.asset_type, "strategy_id": strategy.id},
            )
            for item in self._items.values():
                if item.asset_type == strategy.asset_type and item.id != strategy.id and item.status == "ACTIVE":
                    item.status = "DEPRECATED"
                    self._persist(item)
        strategy.status = status
        self._items[strategy.id] = strategy
        self._persist(strategy)
        return strategy


strategy_repository = StrategyRepository()
