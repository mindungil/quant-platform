from pydantic import BaseModel


class CredentialCreate(BaseModel):
    user_id: str = "anonymous"
    exchange: str
    api_key: str
    api_secret: str
    label: str | None = None
    sandbox: bool = True


class CredentialMaskedResponse(BaseModel):
    user_id: str
    exchange: str
    label: str | None = None
    sandbox: bool = True
    api_key_masked: str
    api_secret_masked: str


class CredentialResponse(BaseModel):
    user_id: str
    exchange: str
    label: str | None = None
    sandbox: bool = True
    api_key: str
    api_secret: str
