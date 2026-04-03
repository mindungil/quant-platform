from fastapi import APIRouter

from app.core.reasoning import build_reasoning_text
from app.core.providers import get_available_providers
from app.models.reasoning import ReasoningRequest, ReasoningResponse

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "llm-gateway",
        "providers": get_available_providers(),
    }


@router.post("/reasoning/generate", response_model=ReasoningResponse)
def generate_reasoning(payload: ReasoningRequest) -> ReasoningResponse:
    return build_reasoning_text(payload)


@router.get("/providers")
def list_providers() -> dict:
    return {"available": get_available_providers()}
