from pydantic import BaseModel, Field


class GatewayPrincipal(BaseModel):
    user_id: str = Field(min_length=1)
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    plan: str = "free"
    forwarded_headers: dict[str, str] = Field(default_factory=dict)


class GatewayWebSocketEnvelope(BaseModel):
    channel: str
    payload: dict
