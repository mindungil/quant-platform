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


# ── Memory Type Queries (Knowledge / Rule / State) ────────────────────

@router.get("/memory/type/{memory_type}")
def get_by_type(
    memory_type: str,
    x_user_id: str | None = Header(default=None),
    asset: str | None = None,
    limit: int = 20,
) -> list[MemoryRecord]:
    """특정 타입의 메모리 조회 (knowledge, rule, state, episode)."""
    records = memory_repository.get_by_type(
        memory_type=memory_type,
        user_id=x_user_id,
        asset=asset,
        limit=limit,
    )
    return records


@router.post("/memory/knowledge")
def store_knowledge(payload: dict, x_user_id: str | None = Header(default=None)) -> MemoryRecord:
    """전략 지식 저장 — 어떤 공식이 어떤 레짐에서 효과적인지."""
    record = MemoryRecord(
        user_id=x_user_id or "system",
        memory_type="knowledge",
        asset=payload.get("asset", "ALL"),
        asset_type=payload.get("asset_type", "crypto"),
        signal_score=0.0,
        action="KNOWLEDGE",
        reasoning=payload.get("content", ""),
        formula_name=payload.get("formula_name"),
        regime_label=payload.get("regime_label"),
        metadata=payload.get("metadata", {}),
    )
    memory_repository.save(record)
    return record


@router.post("/memory/rule")
def store_rule(payload: dict, x_user_id: str | None = Header(default=None)) -> MemoryRecord:
    """고정 규칙 저장 — 매매 규칙, 리스크 제약, 운영 정책."""
    record = MemoryRecord(
        user_id=x_user_id or "system",
        memory_type="rule",
        asset=payload.get("asset", "ALL"),
        asset_type=payload.get("asset_type", "crypto"),
        signal_score=0.0,
        action="RULE",
        reasoning=payload.get("content", ""),
        metadata=payload.get("metadata", {}),
    )
    memory_repository.save(record)
    return record


@router.post("/memory/state")
def store_state(payload: dict, x_user_id: str | None = Header(default=None)) -> MemoryRecord:
    """현재 상태 스냅샷 저장 — 에이전트 판단 컨텍스트."""
    record = MemoryRecord(
        user_id=x_user_id or "system",
        memory_type="state",
        asset=payload.get("asset", "ALL"),
        asset_type=payload.get("asset_type", "crypto"),
        signal_score=payload.get("signal_score", 0.0),
        action=payload.get("action", "STATE"),
        reasoning=payload.get("content", ""),
        formula_name=payload.get("formula_name"),
        regime_label=payload.get("regime_label"),
        metadata=payload.get("metadata", {}),
    )
    memory_repository.save(record)
    return record
