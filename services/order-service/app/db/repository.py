from collections import defaultdict

from app.models.order import OrderResponse


class OrderRepository:
    def __init__(self) -> None:
        self._orders: dict[str, list[OrderResponse]] = defaultdict(list)

    def save(self, user_id: str, response: OrderResponse) -> None:
        self._orders[user_id].append(response)

    def list_for_user(self, user_id: str) -> list[OrderResponse]:
        return self._orders.get(user_id, [])


order_repository = OrderRepository()
