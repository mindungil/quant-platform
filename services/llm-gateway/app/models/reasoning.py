from pydantic import BaseModel, Field


class ReasoningRequest(BaseModel):
    asset: str
    signal_score: float
    strategy_name: str
    memory_count: int = 0
    components: dict[str, float] = Field(default_factory=dict)
    external_context: dict[str, float] = Field(default_factory=dict)
    regime: str | None = None
    formula_name: str | None = None


class ReasoningResponse(BaseModel):
    reasoning: str
    provider: str
    structured: dict | None = None
