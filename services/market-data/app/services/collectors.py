from app.models.candle import CandleCollectorStatus
from app.services.binance_collector import is_enabled as binance_enabled
from app.services.upbit_collector import is_enabled as upbit_enabled


def list_collectors() -> list[CandleCollectorStatus]:
    return [
        CandleCollectorStatus(
            provider="binance",
            asset="BTCUSDT",
            enabled=binance_enabled(),
            mode="websocket" if binance_enabled() else "disabled",
        ),
        CandleCollectorStatus(
            provider="upbit",
            asset="KRW-BTC",
            enabled=upbit_enabled(),
            mode="websocket" if upbit_enabled() else "disabled",
        ),
        CandleCollectorStatus(provider="alpaca", asset="SPY", enabled=False, mode="planned"),
    ]
