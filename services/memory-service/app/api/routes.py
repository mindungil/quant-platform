from fastapi import APIRouter, HTTPException

from app.core.scoring import search_memories
from app.db.repository import memory_repository
from app.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/memory/record", response_model=MemoryRecord)
def record_memory(record: MemoryRecord) -> MemoryRecord:
    memory_repository.save(record)
    return record


@router.post("/memory/search", response_model=MemorySearchResponse)
def search_memory(request: MemorySearchRequest) -> MemorySearchResponse:
    items = search_memories(memory_repository.list_all(), request)
    return MemorySearchResponse(query=request, items=items)


@router.get("/memory/{memory_id}", response_model=MemoryRecord)
def get_memory(memory_id: str) -> MemoryRecord:
    record = memory_repository.get(memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="memory_not_found")
    return record
