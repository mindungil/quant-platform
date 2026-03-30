from fastapi import APIRouter, HTTPException
from app.db.repository import credential_repository
from app.models.credential import CredentialCreate, CredentialResponse

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/credentials", response_model=CredentialResponse)
def store_credential(payload: CredentialCreate) -> CredentialResponse:
    return credential_repository.save(payload)


@router.get("/credentials/{user_id}/{exchange}", response_model=CredentialResponse)
def get_credential(user_id: str, exchange: str) -> CredentialResponse:
    credential = credential_repository.get(user_id, exchange)
    if credential is None:
        raise HTTPException(status_code=404, detail="credential_not_found")
    return credential
