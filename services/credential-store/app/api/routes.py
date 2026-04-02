import os

from fastapi import APIRouter, Header, HTTPException
from app.db.repository import credential_repository
from app.models.credential import CredentialCreate, CredentialMaskedResponse, CredentialResponse
from shared.health import check_sql, health_payload

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return health_payload(
        "credential-store",
        {
            "postgres": check_sql(
                "postgres",
                os.getenv("POSTGRES_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/platform"),
            )
        },
    )


@router.post("/credentials", response_model=CredentialMaskedResponse)
def store_credential(payload: CredentialCreate, x_user_id: str | None = Header(default=None)) -> CredentialMaskedResponse:
    if x_user_id is not None:
        payload.user_id = x_user_id
    return credential_repository.save(payload)


@router.get("/credentials/{user_id}", response_model=list[CredentialMaskedResponse])
def list_credentials(user_id: str, x_user_id: str | None = Header(default=None)) -> list[CredentialMaskedResponse]:
    effective_user = x_user_id or user_id
    return credential_repository.list_for_user(effective_user)


@router.delete("/credentials/{user_id}/{exchange}")
def delete_credential(user_id: str, exchange: str, x_user_id: str | None = Header(default=None)) -> dict:
    effective_user = x_user_id or user_id
    credential_repository.delete(effective_user, exchange)
    return {"status": "deleted", "exchange": exchange}


@router.get("/credentials/{user_id}/{exchange}", response_model=CredentialMaskedResponse)
def get_credential(user_id: str, exchange: str, x_user_id: str | None = Header(default=None)) -> CredentialMaskedResponse:
    if x_user_id is not None and x_user_id != user_id:
        raise HTTPException(status_code=404, detail="credential_not_found")
    credential = credential_repository.get_masked(user_id, exchange)
    if credential is None:
        raise HTTPException(status_code=404, detail="credential_not_found")
    return credential


@router.get("/credentials/{user_id}/{exchange}/reveal", response_model=CredentialResponse)
def reveal_credential(user_id: str, exchange: str, x_user_id: str | None = Header(default=None)) -> CredentialResponse:
    if x_user_id is not None and x_user_id != user_id:
        raise HTTPException(status_code=404, detail="credential_not_found")
    credential = credential_repository.get(user_id, exchange)
    if credential is None:
        raise HTTPException(status_code=404, detail="credential_not_found")
    return credential
