from datetime import datetime, timedelta, timezone

UTC = timezone.utc

from app.core.indicators import calculate_features
from app.models.feature import CandlePayload


def test_calculate_features_returns_latest_snapshot() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(30):
        base = 100 + index
        candles.append(
            CandlePayload(
                timestamp=start + timedelta(minutes=index),
                open=base,
                high=base + 1,
                low=base - 1,
                close=base + 0.5,
                volume=10 + index,
            )
        )

    features = calculate_features(asset="BTCUSDT", candles=candles)

    assert features.asset == "BTCUSDT"
    assert features.close == 129.5
    assert features.ema_9 is not None
    assert features.rsi_14 is not None
