from datetime import datetime
from app.core.market_hours import is_korean_market_open


def test_market_hours_true_during_session() -> None:
    assert is_korean_market_open(datetime(2026, 3, 30, 10, 0)) is True
