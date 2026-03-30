from pydantic import BaseModel


class CredentialCreate(BaseModel):
    user_id: str
    exchange: str
    api_key: str
    api_secret: str


class CredentialResponse(BaseModel):
    user_id: str
    exchange: str
    api_key: str
    api_secret: str
