import hashlib
import hmac
import os
from datetime import UTC, datetime

from app.core.config import settings
from app.models.exchange import ExchangeAuditRecord, ExchangeOrderRequest, ExchangeOrderResponse
from shared.persistence import SqlStore, deserialize_json, serialize_json


class ExchangeRepository:
    def __init__(self) -> None:
        self._failure_counts: dict[tuple[str, str], int] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", settings.postgres_url))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS exchange_order_audits (
                audit_id BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity DOUBLE PRECISION NOT NULL,
                requested_notional DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL,
                shadow_mode BOOLEAN NOT NULL,
                circuit_state TEXT NOT NULL,
                correlation_id TEXT,
                request_payload JSONB NOT NULL,
                response_payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    def _signature(self, payload: ExchangeOrderRequest) -> str:
        if not payload.api_secret:
            return hashlib.sha256(
                f"{payload.exchange}:{payload.asset}:{payload.side}:{payload.quantity}:{payload.requested_notional}".encode("utf-8")
            ).hexdigest()
        message = (
            f"symbol={payload.asset}&side={payload.side}&quantity={payload.quantity}&notional={payload.requested_notional}"
        )
        return hmac.new(payload.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def _record_audit(self, payload: ExchangeOrderRequest, response: ExchangeOrderResponse) -> int | None:
        self._store.execute(
            """
            INSERT INTO exchange_order_audits (
                user_id, exchange, asset, side, quantity, requested_notional, status, shadow_mode,
                circuit_state, correlation_id, request_payload, response_payload, created_at
            ) VALUES (
                :user_id, :exchange, :asset, :side, :quantity, :requested_notional, :status, :shadow_mode,
                :circuit_state, :correlation_id, CAST(:request_payload AS JSONB), CAST(:response_payload AS JSONB), :created_at
            )
            """,
            {
                "user_id": payload.user_id,
                "exchange": payload.exchange,
                "asset": payload.asset,
                "side": payload.side,
                "quantity": payload.quantity,
                "requested_notional": payload.requested_notional,
                "status": response.status,
                "shadow_mode": payload.shadow_mode,
                "circuit_state": response.circuit_state,
                "correlation_id": payload.correlation_id,
                "request_payload": serialize_json(payload.model_dump(mode="json")),
                "response_payload": serialize_json(response.model_dump(mode="json")),
                "created_at": datetime.now(UTC),
            },
        )
        latest = self._store.fetch_one(
            "SELECT audit_id FROM exchange_order_audits WHERE user_id = :user_id ORDER BY audit_id DESC LIMIT 1",
            {"user_id": payload.user_id},
        )
        return None if latest is None else latest["audit_id"]

    def place(self, payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
        key = (payload.user_id, payload.exchange)
        if self._failure_counts.get(key, 0) >= 5:
            response = ExchangeOrderResponse(
                exchange=payload.exchange,
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                status="REJECTED_CIRCUIT_OPEN",
                shadow_mode=payload.shadow_mode,
                circuit_state="OPEN",
                mode="shadow" if payload.shadow_mode else "live",
                exchange_payload_signature=self._signature(payload),
            )
            response.audit_id = self._record_audit(payload, response)
            return response

        status = "SIMULATED_FILLED" if payload.shadow_mode else "FILLED"
        response = ExchangeOrderResponse(
            exchange=payload.exchange,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status=status,
            shadow_mode=payload.shadow_mode,
            circuit_state="CLOSED",
            mode="shadow" if payload.shadow_mode else "live",
            exchange_payload_signature=self._signature(payload),
        )
        response.audit_id = self._record_audit(payload, response)
        return response

    def list_for_user(self, user_id: str, *, limit: int = 50) -> list[ExchangeAuditRecord]:
        rows = self._store.fetch_all(
            """
            SELECT audit_id, user_id, exchange, asset, side, quantity, requested_notional, status, shadow_mode,
                   circuit_state, correlation_id, request_payload, response_payload, created_at
            FROM exchange_order_audits
            WHERE user_id = :user_id
            ORDER BY created_at DESC, audit_id DESC
            LIMIT :limit
            """,
            {"user_id": user_id, "limit": limit},
        )
        return [
            ExchangeAuditRecord(
                audit_id=row["audit_id"],
                user_id=row["user_id"],
                exchange=row["exchange"],
                asset=row["asset"],
                side=row["side"],
                quantity=row["quantity"],
                requested_notional=row["requested_notional"],
                status=row["status"],
                shadow_mode=bool(row["shadow_mode"]),
                circuit_state=row["circuit_state"],
                request_payload=deserialize_json(row["request_payload"]) or {},
                response_payload=deserialize_json(row["response_payload"]) or {},
                correlation_id=row.get("correlation_id"),
                created_at=row["created_at"],
            )
            for row in rows
        ]


exchange_repository = ExchangeRepository()
