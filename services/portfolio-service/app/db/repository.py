from collections import defaultdict

from app.models.portfolio import PortfolioSnapshot, PositionUpdate


class PortfolioRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, float]] = defaultdict(dict)

    def apply(self, payload: PositionUpdate) -> PortfolioSnapshot:
        current = self._items[payload.user_id].get(payload.asset, 0.0)
        signed_quantity = payload.quantity if payload.side == "BUY" else -payload.quantity
        self._items[payload.user_id][payload.asset] = round(current + signed_quantity, 8)
        return self.get(payload.user_id)

    def get(self, user_id: str) -> PortfolioSnapshot:
        return PortfolioSnapshot(user_id=user_id, positions=self._items.get(user_id, {}))


portfolio_repository = PortfolioRepository()
