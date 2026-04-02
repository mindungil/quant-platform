from fastapi import APIRouter, Header, HTTPException

from app.core.scoring import search_formula_outcomes, search_memories
from app.db.repository import memory_repository
from app.models.memory import FormulaOutcomeSearchRequest, MemoryRecord, MemorySearchRequest, MemorySearchResponse, MemorySearchResult

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


@router.post("/memory/search/formula-outcomes", response_model=MemorySearchResponse)
def search_formula_outcomes_endpoint(
    request: FormulaOutcomeSearchRequest,
    x_user_id: str | None = Header(default=None),
) -> MemorySearchResponse:
    records = memory_repository.list_all(user_id=x_user_id)
    results = search_formula_outcomes(records, request)
    return MemorySearchResponse(query=request, items=results)


@router.post("/memory/search/semantic")
def search_semantic(
    request: MemorySearchRequest,
    x_user_id: str | None = Header(default=None),
) -> MemorySearchResponse:
    """Search memories using vector similarity (pgvector)."""
    query_data = {
        "asset": request.asset,
        "action": request.action,
        "signal_score": request.signal_score,
        "formula_name": getattr(request, "formula_name", None),
        "regime_label": getattr(request, "regime_label", None),
    }
    query_embedding = memory_repository._compute_embedding(query_data)
    records = memory_repository.search_similar(
        query_embedding, user_id=x_user_id, top_k=request.top_k
    )
    results = [MemorySearchResult(record=r, score=0.9) for r in records]
    return MemorySearchResponse(query=request, items=results)


@router.post("/memory/{memory_id}/reinforce")
def reinforce_memory(
    memory_id: str,
    payload: dict,
) -> dict:
    trade_outcome = payload.get("trade_outcome", 0.0)
    outcome_sharpe = payload.get("outcome_sharpe", 0.0)
    memory_repository.reinforce(memory_id, trade_outcome, outcome_sharpe)
    return {"status": "reinforced", "memory_id": memory_id}
