from datetime import datetime, timedelta, timezone

UTC = timezone.utc

from app.core.validator import validate_candle_transition
from app.models.candle import CandlePayload


def test_rejects_non_monotonic_timestamp() -> None:
    now = datetime.now(UTC)
    previous = CandlePayload(
        timestamp=now,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
    )
    current = CandlePayload(
        timestamp=now,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
    )

    result = validate_candle_transition(previous, current)

    assert result.accepted is False
    assert result.reason == "non_monotonic_timestamp"


def test_quarantines_large_spike() -> None:
    now = datetime.now(UTC)
    previous = CandlePayload(
        timestamp=now,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
    )
    current = CandlePayload(
        timestamp=now + timedelta(minutes=1),
        open=111,
        high=112,
        low=110,
        close=111,
        volume=1,
    )

    result = validate_candle_transition(previous, current, asset="BTCUSDT")

    assert result.accepted is False
    assert result.anomaly_detected is True
    assert result.reason.startswith("spike_quarantined:")
