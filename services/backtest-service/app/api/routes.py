from fastapi import APIRouter
from app.core.evaluator import evaluate_strategy
from app.models.backtest import BacktestRequest, BacktestResult

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/backtests/run", response_model=BacktestResult)
def run_backtest(payload: BacktestRequest) -> BacktestResult:
    return evaluate_strategy(payload)
