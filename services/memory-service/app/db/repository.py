from app.models.memory import MemoryRecord


class MemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, MemoryRecord] = {}

    def save(self, record: MemoryRecord) -> None:
        self._items[record.id] = record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._items.get(memory_id)

    def list_all(self, user_id: str | None = None) -> list[MemoryRecord]:
        items = list(self._items.values())
        if user_id is None:
            return items
        return [item for item in items if item.user_id == user_id]


memory_repository = MemoryRepository()
