from datetime import datetime
from app.core.market_hours import is_market_open


def test_us_market_hours_true_during_session() -> None:
    assert is_market_open(datetime(2026, 3, 30, 10, 0)) is True
