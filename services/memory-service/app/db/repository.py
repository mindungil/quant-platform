from app.models.memory import MemoryRecord


class MemoryRepository:
    def __init__(self) -> None:
        self._items: dict[str, MemoryRecord] = {}

    def save(self, record: MemoryRecord) -> None:
        self._items[record.id] = record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._items.get(memory_id)

    def list_all(self) -> list[MemoryRecord]:
        return list(self._items.values())


memory_repository = MemoryRepository()
