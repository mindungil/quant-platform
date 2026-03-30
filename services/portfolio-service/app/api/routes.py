from fastapi import APIRouter
from app.db.repository import portfolio_repository
from app.models.portfolio import PositionUpdate, PortfolioSnapshot

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/portfolio/fills", response_model=PortfolioSnapshot)
def apply_fill(payload: PositionUpdate) -> PortfolioSnapshot:
    return portfolio_repository.apply(payload)


@router.get("/portfolio/{user_id}", response_model=PortfolioSnapshot)
def get_portfolio(user_id: str) -> PortfolioSnapshot:
    return portfolio_repository.get(user_id)
