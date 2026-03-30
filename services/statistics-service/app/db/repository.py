from collections import defaultdict

from app.core.engine import compute_statistics
from app.models.statistics import StatisticsInput, StatisticsSnapshot


class StatisticsRepository:
    def __init__(self) -> None:
        self._trade_pnls: dict[str, list[float]] = defaultdict(list)
        self._expected_returns: dict[str, float] = defaultdict(float)

    def record_trade(self, user_id: str, pnl: float, expected_return: float = 0.0) -> StatisticsSnapshot:
        self._trade_pnls[user_id].append(pnl)
        self._expected_returns[user_id] = expected_return
        return self.get(user_id)

    def get(self, user_id: str) -> StatisticsSnapshot:
        return compute_statistics(
            StatisticsInput(
                user_id=user_id,
                trade_pnls=self._trade_pnls.get(user_id, []),
                expected_return=self._expected_returns.get(user_id, 0.0),
            )
        )


statistics_repository = StatisticsRepository()
