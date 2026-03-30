from app.models.exchange import ExchangeOrderRequest, ExchangeOrderResponse


class ExchangeRepository:
    def place(self, payload: ExchangeOrderRequest) -> ExchangeOrderResponse:
        status = "SIMULATED_FILLED" if payload.shadow_mode else "FILLED"
        return ExchangeOrderResponse(
            exchange=payload.exchange,
            asset=payload.asset,
            side=payload.side,
            quantity=payload.quantity,
            status=status,
            shadow_mode=payload.shadow_mode,
        )


exchange_repository = ExchangeRepository()
