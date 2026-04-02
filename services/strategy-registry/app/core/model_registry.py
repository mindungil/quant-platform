"""Quant Model Registry -- versioned formula/strategy configurations.

Stores model definitions as JSON with version history and backtest performance.
Models can be rolled back, compared, and auto-promoted based on performance.
"""
from __future__ import annotations

import logging
import os
from uuid import uuid4

from shared.persistence import SqlStore, serialize_json, deserialize_json

logger = logging.getLogger("strategy-registry")


class ModelRegistry:
    def __init__(self) -> None:
        self._store = SqlStore(
            os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform")
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute("""
            CREATE TABLE IF NOT EXISTS quant_models (
                model_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version INT NOT NULL DEFAULT 1,
                formula_name TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'crypto',
                parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
                indicators JSONB NOT NULL DEFAULT '[]'::jsonb,
                weights JSONB NOT NULL DEFAULT '{}'::jsonb,
                thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
                backtest_sharpe DOUBLE PRECISION,
                backtest_mdd DOUBLE PRECISION,
                backtest_win_rate DOUBLE PRECISION,
                backtest_profit_factor DOUBLE PRECISION,
                live_sharpe DOUBLE PRECISION,
                live_trades INT DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                parent_model_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

    def register(self, model_data: dict) -> dict:
        """Register a new model version."""
        model_id = str(uuid4())
        name = model_data.get("name", "unnamed")

        # Find latest version for this name
        row = self._store.fetch_one(
            "SELECT MAX(version) as max_v FROM quant_models WHERE name = :name",
            {"name": name},
        )
        version = (row["max_v"] or 0) + 1 if row else 1

        self._store.execute("""
            INSERT INTO quant_models (
                model_id, name, version, formula_name, asset_type,
                parameters, indicators, weights, thresholds,
                status, parent_model_id, created_at, updated_at
            ) VALUES (
                :model_id, :name, :version, :formula_name, :asset_type,
                CAST(:parameters AS JSONB), CAST(:indicators AS JSONB),
                CAST(:weights AS JSONB), CAST(:thresholds AS JSONB),
                :status, :parent_model_id, NOW(), NOW()
            )
        """, {
            "model_id": model_id,
            "name": name,
            "version": version,
            "formula_name": model_data.get("formula_name", "composite_adaptive"),
            "asset_type": model_data.get("asset_type", "crypto"),
            "parameters": serialize_json(model_data.get("parameters", {})),
            "indicators": serialize_json(model_data.get("indicators", [])),
            "weights": serialize_json(model_data.get("weights", {})),
            "thresholds": serialize_json(model_data.get("thresholds", {})),
            "status": model_data.get("status", "DRAFT"),
            "parent_model_id": model_data.get("parent_model_id"),
        })

        return {"model_id": model_id, "name": name, "version": version, "status": "DRAFT"}

    def get(self, model_id: str) -> dict | None:
        row = self._store.fetch_one(
            "SELECT * FROM quant_models WHERE model_id = :id", {"id": model_id}
        )
        return self._hydrate(row) if row else None

    def get_active(self, name: str) -> dict | None:
        """Get the active version of a named model."""
        row = self._store.fetch_one(
            "SELECT * FROM quant_models WHERE name = :name AND status = 'ACTIVE' ORDER BY version DESC LIMIT 1",
            {"name": name},
        )
        return self._hydrate(row) if row else None

    def list_models(self, asset_type: str | None = None) -> list[dict]:
        if asset_type:
            rows = self._store.fetch_all(
                "SELECT * FROM quant_models WHERE asset_type = :at ORDER BY name, version DESC",
                {"at": asset_type},
            )
        else:
            rows = self._store.fetch_all(
                "SELECT * FROM quant_models ORDER BY name, version DESC"
            )
        return [self._hydrate(r) for r in rows]

    def update_backtest_results(self, model_id: str, results: dict) -> dict | None:
        self._store.execute("""
            UPDATE quant_models SET
                backtest_sharpe = :sharpe,
                backtest_mdd = :mdd,
                backtest_win_rate = :win_rate,
                backtest_profit_factor = :pf,
                status = CASE WHEN :sharpe >= 0.8 AND :mdd <= 0.20 THEN 'TESTED' ELSE status END,
                updated_at = NOW()
            WHERE model_id = :id
        """, {
            "id": model_id,
            "sharpe": results.get("sharpe_ratio", 0),
            "mdd": results.get("max_drawdown", 0),
            "win_rate": results.get("win_rate", 0),
            "pf": results.get("profit_factor", 0),
        })
        return self.get(model_id)

    def promote(self, model_id: str) -> dict | None:
        """Promote a TESTED model to ACTIVE, deprecating previous active version."""
        model = self.get(model_id)
        if not model or model["status"] not in ("TESTED", "SHADOW"):
            return None
        # Deprecate current active
        self._store.execute(
            "UPDATE quant_models SET status = 'DEPRECATED', updated_at = NOW() WHERE name = :name AND status = 'ACTIVE'",
            {"name": model["name"]},
        )
        # Activate new
        self._store.execute(
            "UPDATE quant_models SET status = 'ACTIVE', updated_at = NOW() WHERE model_id = :id",
            {"id": model_id},
        )
        return self.get(model_id)

    def rollback(self, name: str) -> dict | None:
        """Rollback to previous version."""
        rows = self._store.fetch_all(
            "SELECT * FROM quant_models WHERE name = :name AND status = 'DEPRECATED' ORDER BY version DESC LIMIT 1",
            {"name": name},
        )
        if not rows:
            return None
        prev = self._hydrate(rows[0])
        return self.promote(prev["model_id"])

    def _hydrate(self, row: dict) -> dict:
        return {
            "model_id": row["model_id"],
            "name": row["name"],
            "version": row["version"],
            "formula_name": row["formula_name"],
            "asset_type": row["asset_type"],
            "parameters": deserialize_json(row.get("parameters", "{}")),
            "indicators": deserialize_json(row.get("indicators", "[]")),
            "weights": deserialize_json(row.get("weights", "{}")),
            "thresholds": deserialize_json(row.get("thresholds", "{}")),
            "backtest_sharpe": row.get("backtest_sharpe"),
            "backtest_mdd": row.get("backtest_mdd"),
            "backtest_win_rate": row.get("backtest_win_rate"),
            "backtest_profit_factor": row.get("backtest_profit_factor"),
            "live_sharpe": row.get("live_sharpe"),
            "live_trades": row.get("live_trades", 0),
            "status": row["status"],
            "parent_model_id": row.get("parent_model_id"),
            "created_at": str(row.get("created_at", "")),
            "updated_at": str(row.get("updated_at", "")),
        }


model_registry = ModelRegistry()
