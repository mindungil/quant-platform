from app.models.exchange import ExchangeOrderRequest, ExchangeOrderResponse


class ExchangeRepository:
    def __init__(self) -> None:
        self._failure_counts: dict[tuple[str, str], int] = {}

    def place(self, payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
        key = (payload.user_id, payload.exchange)
        if self._failure_counts.get(key, 0) >= 5:
            return ExchangeOrderResponse(
                exchange=payload.exchange,
                asset=payload.asset,
                side=payload.side,
                quantity=payload.quantity,
                status="REJECTED_CIRCUIT_OPEN",
                shadow_mode=payload.shadow_mode,
                circuit_state="OPEN",
            )

        status = "SIMULATED_FILLED" if payload.shadow_mode else "FILLED"
        return ExchangeOrderResponse(
            exchange=payload.exchange,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status=status,
            shadow_mode=payload.shadow_mode,
            circuit_state="CLOSED",
        )


exchange_repository = ExchangeRepository()
