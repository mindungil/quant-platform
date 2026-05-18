import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

UTC = timezone.utc

import httpx

from app.adapters.base import ExchangeAdapter
from app.adapters.binance import BinanceAdapter
from app.adapters.upbit import UpbitAdapter
from app.adapters.bithumb import BithumbAdapter
from app.adapters.alpaca import AlpacaAdapter
from app.core.config import settings
from app.models.exchange import (
    BalanceResponse,
    CancelOrderRequest,
    CancelOrderResponse,
    ExchangeAuditRecord,
    ExchangeOrderRequest,
    ExchangeOrderResponse,
    OrderbookResponse,
    PositionsResponse,
)
from shared.internal_admin import build_internal_admin_headers
from shared.persistence import SqlStore, deserialize_json, serialize_json

logger = logging.getLogger(__name__)


def _build_adapter_registry() -> dict[str, ExchangeAdapter]:
    adapters: list[ExchangeAdapter] = [
        BinanceAdapter(),
        UpbitAdapter(),
        BithumbAdapter(),
        AlpacaAdapter(),
    ]
    return {a.name: a for a in adapters}


class ExchangeRepository:
    def __init__(self) -> None:
        self._failure_counts: dict[tuple[str, str], int] = {}
        self._store = SqlStore(os.getenv("POSTGRES_URL", settings.postgres_url))
        self._adapters = _build_adapter_registry()
        self._credential_store_url = os.getenv("CREDENTIAL_STORE_BASE_URL", "http://localhost:8010")
        self._ensure_schema()

    def _fetch_credential(self, user_id: str, exchange: str) -> dict | None:
        """credential-store에서 API 키를 자동 조회 (internal secret 인증)."""
        try:
            path = f"/credentials/{user_id}/{exchange}/reveal"
            headers = build_internal_admin_headers(
                settings.internal_admin_secret,
                user_id,
                path,
            )
            resp = httpx.get(
                f"{self._credential_store_url}{path}",
                headers=headers,
                timeout=5.0,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("credential_fetch_failed user=%s exchange=%s", user_id, exchange)
            return None

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

    @staticmethod
    def _default_fill_price(payload: ExchangeOrderRequest) -> float:
        # D17b: prefer the requested price (truth) over back-computing from
        # notional/quantity (which loses precision at micro-notional scale —
        # round(qty, 6) collapses BTC quantities to 0 and ETH quantities to
        # near-integer multiples, producing fake fill prices).
        price = getattr(payload, "price", 0.0) or 0.0
        if price > 0:
            return float(price)
        if payload.quantity > 0 and payload.requested_notional > 0:
            return round(payload.requested_notional / payload.quantity, 8)
        return 0.0

    @classmethod
    def _normalize_response(
        cls,
        payload: ExchangeOrderRequest,
        *,
        status: str,
        adapter_name: str,
        mode: str,
        shadow_mode: bool,
        circuit_state: str = "CLOSED",
        result: dict | None = None,
    ) -> ExchangeOrderResponse:
        result = result or {}
        raw_status = result.get("status") or status
        filled_quantity = float(result.get("filled_quantity", result.get("executed_qty", 0.0)) or 0.0)
        average_fill_price = float(
            result.get("average_fill_price", result.get("avg_fill_price", result.get("fill_price", 0.0))) or 0.0
        )
        if status in {"FILLED", "SIMULATED_FILLED"} and filled_quantity <= 0:
            filled_quantity = float(payload.quantity)
        if filled_quantity > 0 and average_fill_price <= 0:
            average_fill_price = cls._default_fill_price(payload)
        fill_status = "PARTIAL" if 0.0 < filled_quantity < float(payload.quantity) else ("FILLED" if filled_quantity > 0 else "NONE")
        return ExchangeOrderResponse(
            order_id=str(result.get("order_id") or result.get("client_order_id") or uuid4()),
            exchange=payload.exchange,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status=status,
            shadow_mode=shadow_mode,
            exchange_order_id=result.get("exchange_order_id") or result.get("order_id"),
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            fill_status=result.get("fill_status") or fill_status,
            fees=float(result.get("fees", 0.0) or 0.0),
            raw_exchange_status=raw_status,
            circuit_state=circuit_state,
            mode=mode,
            adapter_name=adapter_name,
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
                "request_payload": serialize_json({k: v for k, v in payload.model_dump(mode="json").items() if k not in ("api_key", "api_secret", "secret", "password")}),
                "response_payload": serialize_json(response.model_dump(mode="json")),
                "created_at": datetime.now(UTC),
            },
        )
        latest = self._store.fetch_one(
            "SELECT audit_id FROM exchange_order_audits WHERE user_id = :user_id ORDER BY audit_id DESC LIMIT 1",
            {"user_id": payload.user_id},
        )
        return None if latest is None else latest["audit_id"]

    def _get_adapter(self, exchange: str) -> ExchangeAdapter | None:
        return self._adapters.get(exchange.lower())

    def place(self, payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
        key = (payload.user_id, payload.exchange)
        mode = "shadow" if payload.shadow_mode else "live"
        adapter = self._get_adapter(payload.exchange)
        adapter_name = adapter.name if adapter else "simulated"

        # Circuit breaker check
        if self._failure_counts.get(key, 0) >= 5:
            response = self._normalize_response(
                payload,
                status="REJECTED_CIRCUIT_OPEN",
                adapter_name=adapter_name,
                mode=mode,
                shadow_mode=payload.shadow_mode,
                circuit_state="OPEN",
            )
            response.exchange_payload_signature = self._signature(payload)
            response.audit_id = self._record_audit(payload, response)
            return response

        # Shadow mode: simulate regardless of adapter availability
        if payload.shadow_mode:
            response = self._normalize_response(
                payload,
                status="SIMULATED_FILLED",
                adapter_name=adapter_name,
                mode="shadow",
                shadow_mode=True,
                result={
                    "order_id": str(uuid4()),
                    "exchange_order_id": f"shadow-{uuid4()}",
                    "filled_quantity": payload.quantity,
                    "average_fill_price": self._default_fill_price(payload),
                    "fill_status": "FILLED",
                    "status": "SIMULATED_FILLED",
                },
            )
            response.exchange_payload_signature = self._signature(payload)
            response.audit_id = self._record_audit(payload, response)
            return response

        # Live mode: dispatch to the real adapter
        if adapter is None:
            response = self._normalize_response(
                payload,
                status="REJECTED_NO_ADAPTER",
                adapter_name="none",
                mode="live",
                shadow_mode=False,
            )
            response.exchange_payload_signature = self._signature(payload)
            response.audit_id = self._record_audit(payload, response)
            return response

        try:
            result = adapter.place_order(
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                notional=payload.requested_notional,
                api_key=payload.api_key,
                api_secret=payload.api_secret,
                sandbox=payload.sandbox,
            )
            status = result.get("status", "FILLED")
            self._failure_counts[key] = 0
        except NotImplementedError:
            status = "REJECTED_NOT_IMPLEMENTED"
            result = {}
            self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
            logger.warning("adapter %s is not implemented", adapter.name)
        except Exception:
            status = "REJECTED_EXCHANGE_ERROR"
            result = {}
            self._failure_counts[key] = self._failure_counts.get(key, 0) + 1
            logger.exception("adapter %s failed", adapter.name)

        response = self._normalize_response(
            payload,
            status=status,
            adapter_name=adapter_name,
            mode="live",
            shadow_mode=False,
            result=result,
        )
        response.exchange_payload_signature = self._signature(payload)
        response.audit_id = self._record_audit(payload, response)
        return response

    def cancel_order(self, order_id: str, payload: CancelOrderRequest) -> CancelOrderResponse:
        adapter = self._get_adapter(payload.exchange)

        if payload.shadow_mode:
            return CancelOrderResponse(
                order_id=order_id,
                status="SIMULATED_CANCELED",
                exchange=payload.exchange,
                shadow_mode=True,
            )

        if adapter is None:
            return CancelOrderResponse(
                order_id=order_id,
                status="REJECTED_NO_ADAPTER",
                exchange=payload.exchange,
                shadow_mode=False,
            )

        try:
            result = adapter.cancel_order(
                order_id=order_id,
                user_id=payload.user_id,
                exchange=payload.exchange,
                api_key=payload.api_key,
                api_secret=payload.api_secret,
            )
            status = result.get("status", "CANCELED")
        except NotImplementedError:
            status = "REJECTED_NOT_IMPLEMENTED"
        except Exception:
            status = "REJECTED_EXCHANGE_ERROR"
            logger.exception("cancel_order failed for %s", payload.exchange)

        return CancelOrderResponse(
            order_id=order_id,
            status=status,
            exchange=payload.exchange,
            shadow_mode=False,
        )

    def get_balance(self, user_id: str, exchange: str, *, api_key: str | None = None, api_secret: str | None = None, shadow_mode: bool = False) -> BalanceResponse:
        adapter = self._get_adapter(exchange)

        if shadow_mode:
            return BalanceResponse(
                user_id=user_id,
                exchange=exchange,
                balances=[],
                shadow_mode=True,
            )

        if adapter is None:
            return BalanceResponse(
                user_id=user_id,
                exchange=exchange,
                balances=[],
                shadow_mode=False,
            )

        # api_key 미전달 시 credential-store에서 자동 조회
        if not api_key:
            cred = self._fetch_credential(user_id, exchange)
            if cred:
                api_key = cred.get("api_key")
                api_secret = cred.get("api_secret")

        try:
            result = adapter.get_balance(
                user_id=user_id,
                exchange=exchange,
                api_key=api_key,
                api_secret=api_secret,
            )
            balances = result.get("balances", [])
        except NotImplementedError:
            balances = []
            logger.warning("get_balance not implemented for %s", exchange)
        except Exception:
            balances = []
            logger.exception("get_balance failed for %s", exchange)

        return BalanceResponse(
            user_id=user_id,
            exchange=exchange,
            balances=balances,
            shadow_mode=False,
        )

    def get_positions(self, user_id: str, exchange: str, *, api_key: str | None = None, api_secret: str | None = None, shadow_mode: bool = False) -> PositionsResponse:
        adapter = self._get_adapter(exchange)

        if shadow_mode:
            return PositionsResponse(
                user_id=user_id,
                exchange=exchange,
                positions=[],
                shadow_mode=True,
            )

        if adapter is None:
            return PositionsResponse(
                user_id=user_id,
                exchange=exchange,
                positions=[],
                shadow_mode=False,
            )

        # api_key 미전달 시 credential-store에서 자동 조회
        if not api_key:
            cred = self._fetch_credential(user_id, exchange)
            if cred:
                api_key = cred.get("api_key")
                api_secret = cred.get("api_secret")

        try:
            result = adapter.get_positions(
                user_id=user_id,
                exchange=exchange,
                api_key=api_key,
                api_secret=api_secret,
            )
            positions = result.get("positions", [])
        except NotImplementedError:
            positions = []
            logger.warning("get_positions not implemented for %s", exchange)
        except Exception:
            positions = []
            logger.exception("get_positions failed for %s", exchange)

        return PositionsResponse(
            user_id=user_id,
            exchange=exchange,
            positions=positions,
            shadow_mode=False,
        )

    def verify_credential(self, user_id: str, exchange: str) -> dict:
        """Call adapter.validate_credentials with stored credentials.

        Returns {"ok": bool, "exchange": str, "reason": str|None}.
        Used by the settings UI's 연결 테스트 button.
        """
        adapter = self._get_adapter(exchange)
        if adapter is None:
            return {"ok": False, "exchange": exchange, "reason": "adapter_not_available"}
        cred = self._fetch_credential(user_id, exchange)
        if not cred:
            return {"ok": False, "exchange": exchange, "reason": "credential_not_found"}
        api_key = cred.get("api_key") or ""
        api_secret = cred.get("api_secret") or ""
        try:
            ok = bool(adapter.validate_credentials(api_key, api_secret))
        except Exception as exc:
            logger.warning("verify_credential failed user=%s exchange=%s err=%s", user_id, exchange, exc)
            return {"ok": False, "exchange": exchange, "reason": "exchange_rejected"}
        return {"ok": ok, "exchange": exchange, "reason": None if ok else "invalid_credentials"}

    def get_orderbook(self, asset: str, exchange: str, *, depth: int = 20, shadow_mode: bool = False) -> OrderbookResponse:
        adapter = self._get_adapter(exchange)

        if shadow_mode:
            simulated_bids = [[str(50000 - i * 10), str(0.1)] for i in range(min(depth, 5))]
            simulated_asks = [[str(50000 + i * 10), str(0.1)] for i in range(min(depth, 5))]
            return OrderbookResponse(
                asset=asset,
                exchange=exchange,
                bids=simulated_bids,
                asks=simulated_asks,
            )

        if adapter is None:
            return OrderbookResponse(
                asset=asset,
                exchange=exchange,
                bids=[],
                asks=[],
            )

        try:
            result = adapter.get_orderbook(
                asset=asset,
                exchange=exchange,
                depth=depth,
            )
            bids = result.get("bids", [])
            asks = result.get("asks", [])
        except NotImplementedError:
            bids, asks = [], []
            logger.warning("get_orderbook not implemented for %s", exchange)
        except Exception:
            bids, asks = [], []
            logger.exception("get_orderbook failed for %s", exchange)

        return OrderbookResponse(
            asset=asset,
            exchange=exchange,
            bids=bids,
            asks=asks,
        )

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

    def get_order_status(self, order_id: str) -> dict | None:
        row = self._store.fetch_one(
            """
            SELECT audit_id, user_id, exchange, asset, side, quantity, requested_notional, status, shadow_mode,
                   circuit_state, correlation_id, request_payload, response_payload, created_at
            FROM exchange_order_audits
            WHERE correlation_id = :order_id OR response_payload::jsonb ->> 'order_id' = :order_id
            ORDER BY created_at DESC, audit_id DESC
            LIMIT 1
            """,
            {"order_id": order_id},
        )
        if row is None:
            return None
        return {
            "order_id": order_id,
            "status": row["status"],
            "exchange": row["exchange"],
            "shadow_mode": bool(row["shadow_mode"]),
            "circuit_state": row["circuit_state"],
            "audit_id": row["audit_id"],
            "correlation_id": row.get("correlation_id"),
            "created_at": row["created_at"],
        }

    def get_order_fills(self, order_id: str) -> list[dict]:
        row = self._store.fetch_one(
            """
            SELECT response_payload, created_at
            FROM exchange_order_audits
            WHERE correlation_id = :order_id OR response_payload::jsonb ->> 'order_id' = :order_id
            ORDER BY created_at DESC, audit_id DESC
            LIMIT 1
            """,
            {"order_id": order_id},
        )
        if row is None:
            return []
        payload = deserialize_json(row["response_payload"]) or {}
        filled_quantity = float(payload.get("filled_quantity", 0.0) or 0.0)
        if filled_quantity <= 0:
            return []
        return [{
            "order_id": order_id,
            "exchange_order_id": payload.get("exchange_order_id") or payload.get("order_id"),
            "filled_quantity": filled_quantity,
            "average_fill_price": float(payload.get("average_fill_price", 0.0) or 0.0),
            "fees": float(payload.get("fees", 0.0) or 0.0),
            "fill_status": payload.get("fill_status") or "FILLED",
            "status": payload.get("status"),
            "last_update_at": payload.get("last_update_at") or row["created_at"],
        }]


exchange_repository = ExchangeRepository()
