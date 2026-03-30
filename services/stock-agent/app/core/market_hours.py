from datetime import datetime


def is_market_open(now: datetime | None = None) -> bool:
    current = now or datetime.now()
    return current.weekday() < 5 and ((current.hour > 9 or (current.hour == 9 and current.minute >= 30)) and current.hour < 16)
