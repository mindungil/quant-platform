from collections import defaultdict

from app.models.agent import DecisionRecord


class DecisionRepository:
    def __init__(self) -> None:
        self._items: dict[str, list[DecisionRecord]] = defaultdict(list)

    def save(self, asset: str, record: DecisionRecord) -> None:
        self._items[asset].append(record)

    def get_latest(self, asset: str) -> DecisionRecord | None:
        history = self._items.get(asset, [])
        return history[-1] if history else None

    def get_history(self, asset: str) -> list[DecisionRecord]:
        return self._items.get(asset, [])


decision_repository = DecisionRepository()
