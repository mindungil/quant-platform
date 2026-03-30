from fastapi import APIRouter, Header, HTTPException

from app.core.scoring import search_memories
from app.db.repository import memory_repository
from app.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/memory/record", response_model=MemoryRecord)
def record_memory(record: MemoryRecord, x_user_id: str | None = Header(default=None)) -> MemoryRecord:
    if x_user_id is not None:
        record.user_id = x_user_id
    memory_repository.save(record)
    return record


@router.post("/memory/search", response_model=MemorySearchResponse)
def search_memory(request: MemorySearchRequest, x_user_id: str | None = Header(default=None)) -> MemorySearchResponse:
    if x_user_id is not None:
        request.user_id = x_user_id
    items = search_memories(memory_repository.list_all(user_id=request.user_id), request)
    return MemorySearchResponse(query=request, items=items)


@router.get("/memory/{memory_id}", response_model=MemoryRecord)
def get_memory(memory_id: str, x_user_id: str | None = Header(default=None)) -> MemoryRecord:
    record = memory_repository.get(memory_id)
    if record is None or (x_user_id is not None and record.user_id != x_user_id):
        raise HTTPException(status_code=404, detail="memory_not_found")
    return record
