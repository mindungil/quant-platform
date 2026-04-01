from fastapi import APIRouter

from app.core.snapshot import build_external_context
from app.models.external_data import ExternalContextSnapshot

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/external/context/{asset}", response_model=ExternalContextSnapshot)
def get_external_context(asset: str) -> ExternalContextSnapshot:
    return build_external_context(asset)
