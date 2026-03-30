from app.models.memory import MemoryRecord, MemorySearchRequest, MemorySearchResult


def search_memories(
    records: list[MemoryRecord], request: MemorySearchRequest
) -> list[MemorySearchResult]:
    results: list[MemorySearchResult] = []
    for record in records:
        score = 0.0
        if record.asset == request.asset:
            score += 0.5
        if request.action and record.action == request.action:
            score += 0.3
        if request.strategy_id and record.strategy_id == request.strategy_id:
            score += 0.2
        score -= min(abs(record.signal_score - request.signal_score), 1.0) * 0.2
        if score > 0:
            results.append(MemorySearchResult(score=round(score, 4), record=record))

    results.sort(key=lambda item: item.score, reverse=True)
    return results[: request.top_k]
