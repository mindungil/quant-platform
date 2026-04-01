import os
from datetime import UTC, datetime

from app.models.risk import RiskApprovalRequest, RiskApprovalResponse, RiskIncident
from shared.persistence import RedisStore, SqlStore, deserialize_json, serialize_json
from shared.realtime import RealtimeBus


class RiskRepository:
    def __init__(self) -> None:
        self._store = SqlStore(os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"))
        self._realtime = RealtimeBus(RedisStore(os.getenv("REDIS_URL", "redis://localhost:6379/0")))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_incidents (
                id BIGSERIAL PRIMARY KEY,
                user_id TEXT,
                asset TEXT NOT NULL,
                level TEXT NOT NULL,
                approved BOOLEAN NOT NULL,
                reason TEXT NOT NULL,
                requested_notional DOUBLE PRECISION NOT NULL,
                exposure_ratio DOUBLE PRECISION NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def record(self, payload: RiskApprovalRequest, result: RiskApprovalResponse) -> RiskIncident:
        created_at = datetime.now(UTC)
        self._store.execute(
            """
            INSERT INTO risk_incidents (
                user_id, asset, level, approved, reason, requested_notional, exposure_ratio, payload, created_at
            ) VALUES (
                :user_id, :asset, :level, :approved, :reason, :requested_notional, :exposure_ratio, CAST(:payload AS JSONB), :created_at
            )
            """,
            {
                "user_id": payload.user_id,
                "asset": payload.asset,
                "level": result.level,
                "approved": result.approved,
                "reason": result.reason,
                "requested_notional": payload.requested_notional,
                "exposure_ratio": result.exposure_ratio,
                "payload": serialize_json(payload.model_dump(mode="json")),
                "created_at": created_at,
            },
        )
        incident = RiskIncident(
            user_id=payload.user_id,
            asset=payload.asset,
            level=result.level,
            approved=result.approved,
            reason=result.reason,
            requested_notional=payload.requested_notional,
            exposure_ratio=result.exposure_ratio,
            payload=payload.model_dump(mode="json"),
            created_at=created_at,
        )
        if not result.approved:
            self._realtime.publish(
                event_type="risk.triggered",
                source="risk-service",
                user_id=payload.user_id,
                correlation_id=payload.correlation_id,
                data=incident.model_dump(mode="json"),
            )
        return incident

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[RiskIncident]:
        rows = self._store.fetch_all(
            """
            SELECT user_id, asset, level, approved, reason, requested_notional, exposure_ratio, payload, created_at
            FROM risk_incidents
            WHERE user_id = :user_id
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [
            RiskIncident(
                user_id=row["user_id"],
                asset=row["asset"],
                level=row["level"],
                approved=bool(row["approved"]),
                reason=row["reason"],
                requested_notional=row["requested_notional"],
                exposure_ratio=row["exposure_ratio"],
                payload=deserialize_json(row["payload"]) or {},
                created_at=row["created_at"],
            )
            for row in rows
        ]


risk_repository = RiskRepository()
