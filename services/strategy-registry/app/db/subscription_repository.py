"""Persistence for template subscriptions and lane allocations."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from app.models.subscription import (
    LaneAllocation,
    TemplateSubscription,
    VALID_SUBSCRIPTION_STATUSES,
)
from shared.persistence import SqlStore

UTC = timezone.utc


class SubscriptionRepository:
    def __init__(self) -> None:
        self._store = SqlStore(
            os.getenv(
                "POSTGRES_URL",
                "postgresql+psycopg://postgres:postgres@localhost:5432/platform",
            )
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS user_template_subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                template_id TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'crypto',
                status TEXT NOT NULL DEFAULT 'enabled',
                weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, template_id, asset_type)
            )
            """
        )
        self._store.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_uts_user_enabled
            ON user_template_subscriptions(user_id, asset_type)
            WHERE status = 'enabled'
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS lane_allocations (
                user_id TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'crypto',
                agent_pct DOUBLE PRECISION NOT NULL DEFAULT 0.70,
                template_pct DOUBLE PRECISION NOT NULL DEFAULT 0.30,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, asset_type)
            )
            """
        )
        # Add `lane` column to strategy_records if missing
        self._store.execute(
            "ALTER TABLE strategy_records ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'agent_core'"
        )

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def list_all_enabled(
        self,
        asset_type: str | None = None,
    ) -> list[TemplateSubscription]:
        """Agent-facing: return ALL enabled subscriptions across users.

        Used by the crypto-agent's dual-lane orchestrator to fan out template
        lanes per subscribed user. Not user-filtered — callers should be
        internal services.
        """
        params: dict[str, str] = {}
        conditions = ["status = 'enabled'"]
        if asset_type:
            conditions.append("asset_type = :asset_type")
            params["asset_type"] = asset_type
        where = " AND ".join(conditions)
        rows = self._store.fetch_all(
            f"SELECT * FROM user_template_subscriptions WHERE {where} ORDER BY user_id, created_at",
            params,
        )
        return [self._hydrate(r) for r in rows]

    def list_for_user(
        self,
        user_id: str,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[TemplateSubscription]:
        conditions = ["user_id = :user_id"]
        params: dict[str, str] = {"user_id": user_id}
        if asset_type:
            conditions.append("asset_type = :asset_type")
            params["asset_type"] = asset_type
        if status:
            if status not in VALID_SUBSCRIPTION_STATUSES:
                return []
            conditions.append("status = :status")
            params["status"] = status
        where = " AND ".join(conditions)
        rows = self._store.fetch_all(
            f"SELECT * FROM user_template_subscriptions WHERE {where} ORDER BY created_at DESC",
            params,
        )
        return [self._hydrate(r) for r in rows]

    def get(self, subscription_id: str) -> TemplateSubscription | None:
        row = self._store.fetch_one(
            "SELECT * FROM user_template_subscriptions WHERE id = :id",
            {"id": subscription_id},
        )
        return self._hydrate(row) if row else None

    def create(
        self,
        user_id: str,
        template_id: str,
        asset_type: str = "crypto",
        weight: float = 1.0,
    ) -> TemplateSubscription:
        # Upsert: if a row exists for (user, template, asset_type), flip it to
        # enabled and return it. Otherwise insert.
        existing = self._store.fetch_one(
            """
            SELECT * FROM user_template_subscriptions
            WHERE user_id = :user_id AND template_id = :template_id AND asset_type = :asset_type
            """,
            {"user_id": user_id, "template_id": template_id, "asset_type": asset_type},
        )
        if existing:
            self._store.execute(
                """
                UPDATE user_template_subscriptions
                SET status = 'enabled', weight = :weight, updated_at = NOW()
                WHERE id = :id
                """,
                {"id": existing["id"], "weight": weight},
            )
            updated = self._store.fetch_one(
                "SELECT * FROM user_template_subscriptions WHERE id = :id",
                {"id": existing["id"]},
            )
            return self._hydrate(updated)  # type: ignore[arg-type]

        sub = TemplateSubscription(
            user_id=user_id,
            template_id=template_id,
            asset_type=asset_type,
            weight=weight,
        )
        self._store.execute(
            """
            INSERT INTO user_template_subscriptions
                (id, user_id, template_id, asset_type, status, weight, created_at, updated_at)
            VALUES
                (:id, :user_id, :template_id, :asset_type, :status, :weight, :created_at, :updated_at)
            """,
            {
                "id": sub.id,
                "user_id": sub.user_id,
                "template_id": sub.template_id,
                "asset_type": sub.asset_type,
                "status": sub.status,
                "weight": sub.weight,
                "created_at": sub.created_at,
                "updated_at": sub.updated_at,
            },
        )
        return sub

    def update(
        self,
        subscription_id: str,
        status: str | None = None,
        weight: float | None = None,
    ) -> TemplateSubscription | None:
        sub = self.get(subscription_id)
        if sub is None:
            return None
        sets: list[str] = ["updated_at = NOW()"]
        params: dict[str, object] = {"id": subscription_id}
        if status is not None:
            if status not in VALID_SUBSCRIPTION_STATUSES:
                return None
            sets.append("status = :status")
            params["status"] = status
        if weight is not None:
            sets.append("weight = :weight")
            params["weight"] = weight
        self._store.execute(
            f"UPDATE user_template_subscriptions SET {', '.join(sets)} WHERE id = :id",
            params,
        )
        return self.get(subscription_id)

    def delete(self, subscription_id: str) -> bool:
        existing = self.get(subscription_id)
        if existing is None:
            return False
        self._store.execute(
            "UPDATE user_template_subscriptions SET status = 'stopped', updated_at = NOW() WHERE id = :id",
            {"id": subscription_id},
        )
        return True

    @staticmethod
    def _hydrate(row: dict) -> TemplateSubscription:
        return TemplateSubscription(
            id=row["id"],
            user_id=row["user_id"],
            template_id=row["template_id"],
            asset_type=row.get("asset_type", "crypto"),
            status=row.get("status", "enabled"),
            weight=float(row.get("weight", 1.0)),
            created_at=row.get("created_at") or datetime.now(UTC),
            updated_at=row.get("updated_at") or datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Lane allocations
    # ------------------------------------------------------------------

    def get_allocation(self, user_id: str, asset_type: str = "crypto") -> LaneAllocation:
        row = self._store.fetch_one(
            """
            SELECT * FROM lane_allocations
            WHERE user_id = :user_id AND asset_type = :asset_type
            """,
            {"user_id": user_id, "asset_type": asset_type},
        )
        if row:
            return LaneAllocation(
                user_id=row["user_id"],
                asset_type=row["asset_type"],
                agent_pct=float(row["agent_pct"]),
                template_pct=float(row["template_pct"]),
                updated_at=row.get("updated_at") or datetime.now(UTC),
            )
        return LaneAllocation(user_id=user_id, asset_type=asset_type)

    def upsert_allocation(
        self,
        user_id: str,
        asset_type: str,
        agent_pct: float,
        template_pct: float,
    ) -> LaneAllocation:
        if agent_pct + template_pct > 1.0 + 1e-6:
            raise ValueError("agent_pct + template_pct must be <= 1.0")
        self._store.execute(
            """
            INSERT INTO lane_allocations
                (user_id, asset_type, agent_pct, template_pct, updated_at)
            VALUES
                (:user_id, :asset_type, :agent_pct, :template_pct, NOW())
            ON CONFLICT (user_id, asset_type) DO UPDATE
                SET agent_pct = EXCLUDED.agent_pct,
                    template_pct = EXCLUDED.template_pct,
                    updated_at = NOW()
            """,
            {
                "user_id": user_id,
                "asset_type": asset_type,
                "agent_pct": agent_pct,
                "template_pct": template_pct,
            },
        )
        return self.get_allocation(user_id, asset_type)


subscription_repository = SubscriptionRepository()
