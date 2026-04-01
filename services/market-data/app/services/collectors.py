from app.models.candle import CandleCollectorStatus
from app.services.binance_collector import is_enabled as binance_enabled


def list_collectors() -> list[CandleCollectorStatus]:
    return [
        CandleCollectorStatus(
            provider="binance",
            asset="BTCUSDT",
            enabled=binance_enabled(),
            mode="websocket" if binance_enabled() else "disabled",
        ),
        CandleCollectorStatus(provider="upbit", asset="BTC-KRW", enabled=False, mode="planned"),
        CandleCollectorStatus(provider="alpaca", asset="SPY", enabled=False, mode="planned"),
    ]
