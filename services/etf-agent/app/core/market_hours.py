from datetime import date, datetime

# Korean national holidays (fixed-date holidays).
# Lunar-calendar holidays (Seollal, Chuseok) shift each year; this list
# covers 2024-2027 with the most common observed dates.  Update annually.
KOREAN_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 1), date(2024, 2, 9), date(2024, 2, 10), date(2024, 2, 11), date(2024, 2, 12),
    date(2024, 3, 1), date(2024, 4, 10), date(2024, 5, 1), date(2024, 5, 5), date(2024, 5, 6),
    date(2024, 5, 15), date(2024, 6, 6), date(2024, 8, 15), date(2024, 9, 16), date(2024, 9, 17),
    date(2024, 9, 18), date(2024, 10, 3), date(2024, 10, 9), date(2024, 12, 25), date(2024, 12, 31),
    # 2025
    date(2025, 1, 1), date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 3, 1), date(2025, 3, 3), date(2025, 5, 1), date(2025, 5, 5), date(2025, 5, 6),
    date(2025, 5, 15), date(2025, 6, 6), date(2025, 8, 15), date(2025, 10, 3),
    date(2025, 10, 5), date(2025, 10, 6), date(2025, 10, 7), date(2025, 10, 9), date(2025, 12, 25), date(2025, 12, 31),
    # 2026
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 3, 1), date(2026, 3, 2), date(2026, 5, 1), date(2026, 5, 5), date(2026, 5, 24),
    date(2026, 6, 6), date(2026, 8, 15), date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25),
    date(2026, 9, 26), date(2026, 10, 3), date(2026, 10, 9), date(2026, 12, 25), date(2026, 12, 31),
    # 2027
    date(2027, 1, 1), date(2027, 2, 6), date(2027, 2, 7), date(2027, 2, 8), date(2027, 2, 9),
    date(2027, 3, 1), date(2027, 5, 1), date(2027, 5, 5), date(2027, 5, 13),
    date(2027, 6, 6), date(2027, 8, 15), date(2027, 8, 16), date(2027, 10, 3),
    date(2027, 10, 9), date(2027, 10, 13), date(2027, 10, 14), date(2027, 10, 15), date(2027, 12, 25), date(2027, 12, 31),
}


def is_korean_holiday(d: date) -> bool:
    return d in KOREAN_HOLIDAYS


def is_korean_market_open(now: datetime | None = None) -> bool:
    """Return True when KRX is open: weekdays 09:00-15:30 KST, excluding holidays."""
    current = now or datetime.now()
    if current.weekday() >= 5:
        return False
    if is_korean_holiday(current.date()):
        return False
    return (current.hour > 9 or (current.hour == 9 and current.minute >= 0)) and (
        current.hour < 15 or (current.hour == 15 and current.minute <= 30)
    )
