from pydantic import BaseModel, Field


class PositionUpdate(BaseModel):
    user_id: str
    asset: str
    side: str
    quantity: float


class PortfolioSnapshot(BaseModel):
    user_id: str
    positions: dict[str, float] = Field(default_factory=dict)
