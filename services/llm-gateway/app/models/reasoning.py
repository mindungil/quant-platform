from typing import Any

from pydantic import BaseModel, Field


class ReasoningRequest(BaseModel):
    asset: str
    signal_score: float
    strategy_name: str
    memory_count: int = 0
    components: dict[str, float] = Field(default_factory=dict)
    # external_context values are stringified into the prompt and never used as
    # numeric inputs, so allow heterogeneous types (str, int, list, None) for
    # callers like the anomaly narrator that pass observation strings.
    external_context: dict[str, Any] = Field(default_factory=dict)
    regime: str | None = None
    formula_name: str | None = None


class ReasoningResponse(BaseModel):
    reasoning: str
    provider: str
    structured: dict | None = None
