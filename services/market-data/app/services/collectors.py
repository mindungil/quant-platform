from app.models.candle import CandleCollectorStatus


def list_collectors() -> list[CandleCollectorStatus]:
    return [
        CandleCollectorStatus(provider="binance", asset="BTCUSDT", enabled=True, mode="websocket-ready"),
        CandleCollectorStatus(provider="upbit", asset="BTC-KRW", enabled=False, mode="planned"),
        CandleCollectorStatus(provider="alpaca", asset="SPY", enabled=False, mode="planned"),
    ]
