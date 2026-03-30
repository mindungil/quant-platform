from fastapi import APIRouter

from app.core.reasoning import build_reasoning_text
from app.models.reasoning import ReasoningRequest, ReasoningResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/reasoning/generate", response_model=ReasoningResponse)
def generate_reasoning(payload: ReasoningRequest) -> ReasoningResponse:
    return build_reasoning_text(payload)
