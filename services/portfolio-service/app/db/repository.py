from collections import defaultdict

from app.models.portfolio import PortfolioSnapshot, PositionUpdate


class PortfolioRepository:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, float]] = defaultdict(dict)
        self._prices: dict[str, dict[str, float]] = defaultdict(dict)
        self._fills: dict[str, list[PositionUpdate]] = defaultdict(list)

    def apply(self, payload: PositionUpdate) -> PortfolioSnapshot:
        current = self._items[payload.user_id].get(payload.asset, 0.0)
        signed_quantity = payload.quantity if payload.side == "BUY" else -payload.quantity
        self._items[payload.user_id][payload.asset] = round(current + signed_quantity, 8)
        if payload.side == "BUY" and payload.price > 0:
            self._prices[payload.user_id][payload.asset] = payload.price
        self._fills[payload.user_id].append(payload)
        return self.get(payload.user_id)

    def get(self, user_id: str) -> PortfolioSnapshot:
        positions = self._items.get(user_id, {})
        prices = self._prices.get(user_id, {})
        total_exposure = round(
            sum(abs(quantity) * prices.get(asset, 0.0) for asset, quantity in positions.items()),
            4,
        )
        return PortfolioSnapshot(
            user_id=user_id,
            positions=positions,
            average_entry_prices=prices,
            recent_fills=self._fills.get(user_id, [])[-10:],
            total_exposure=total_exposure,
            rebalance_needed=total_exposure > 100000,
        )


portfolio_repository = PortfolioRepository()
