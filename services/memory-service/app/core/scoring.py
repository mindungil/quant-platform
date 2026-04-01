from app.models.memory import FormulaOutcomeSearchRequest, MemoryRecord, MemorySearchRequest, MemorySearchResult


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


def search_formula_outcomes(
    records: list[MemoryRecord],
    request: FormulaOutcomeSearchRequest,
) -> list[MemorySearchResult]:
    """Search memory for formula performance in similar market regimes.

    Scoring: regime match (0.4), asset match (0.3), has outcome (0.2), recency (0.1)
    """
    from datetime import UTC, datetime

    scored: list[tuple[float, MemoryRecord]] = []
    now = datetime.now(UTC)

    for record in records:
        if record.formula_name is None:
            continue  # skip non-formula records
        if request.formula_name and record.formula_name != request.formula_name:
            continue

        score = 0.0

        # Regime match (0.4)
        if record.regime_label and request.regime_label:
            if record.regime_label == request.regime_label:
                score += 0.4
            else:
                req_parts = set(request.regime_label.split("_"))
                rec_parts = set(record.regime_label.split("_"))
                overlap = len(req_parts & rec_parts) / max(len(req_parts | rec_parts), 1)
                score += 0.4 * overlap

        # Asset match (0.3)
        if request.asset and record.asset == request.asset:
            score += 0.3
        elif request.asset is None:
            score += 0.15  # no filter = half bonus

        # Has trade outcome (0.2)
        if record.trade_outcome is not None:
            score += 0.2

        # Recency (0.1) - more recent = higher score
        if record.timestamp:
            ts = record.timestamp
            if ts.tzinfo is None:
                from datetime import timezone

                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (now - ts).total_seconds() / 86400
            recency = max(0, 1 - age_days / 90)  # decay over 90 days
            score += 0.1 * recency

        scored.append((score, record))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        MemorySearchResult(record=record, score=round(sc, 4))
        for sc, record in scored[: request.top_k]
    ]
