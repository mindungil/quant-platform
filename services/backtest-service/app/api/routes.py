from fastapi import APIRouter, HTTPException
from app.core.evaluator import evaluate_strategy, get_job, submit_job
from app.models.backtest import BacktestJob, BacktestRequest, BacktestResult

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/backtests/run", response_model=BacktestJob)
async def run_backtest(payload: BacktestRequest) -> BacktestJob:
    """Submit a backtest job. Returns immediately with a job_id to poll."""
    return submit_job(payload)


@router.get("/backtests/{job_id}", response_model=BacktestJob)
def get_backtest(job_id: str) -> BacktestJob:
    """Poll for backtest job status and results."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job
