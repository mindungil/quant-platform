import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.models.backtest import BacktestJob, BacktestRequest, BacktestResult

logger = logging.getLogger(__name__)


def evaluate_strategy(payload: BacktestRequest) -> BacktestResult:
    weight_count = max(len(payload.weights), 1)
    sharpe = round(1.0 + ((sum(payload.weights.values()) / weight_count) * 0.2), 4)
    mdd = round(max(0.02, 0.18 - (payload.sample_size / 5000)), 4)
    win_rate = round(min(0.9, 0.45 + (payload.sample_size / 5000)), 4)
    status = "PASSED" if sharpe >= 1.1 and mdd <= 0.15 and win_rate >= 0.52 else "FAILED"
    return BacktestResult(
        strategy_id=payload.strategy_id,
        sharpe_ratio=sharpe,
        sortino_ratio=round(sharpe + 0.1, 4),
        max_drawdown=mdd,
        win_rate=win_rate,
        status=status,
    )


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

_jobs: dict[str, BacktestJob] = {}

# Optional callback for publishing events (set by routes at startup)
_event_callback = None


def set_event_callback(cb):  # noqa: ANN001
    global _event_callback
    _event_callback = cb


def get_job(job_id: str) -> BacktestJob | None:
    return _jobs.get(job_id)


def submit_job(payload: BacktestRequest) -> BacktestJob:
    """Create a job entry and schedule async evaluation. Returns immediately."""
    job_id = str(uuid4())
    job = BacktestJob(job_id=job_id, strategy_id=payload.strategy_id, status="PENDING")
    _jobs[job_id] = job

    # Fire-and-forget the actual evaluation on the running event loop
    asyncio.get_event_loop().create_task(_run_job(job_id, payload))
    return job


async def _run_job(job_id: str, payload: BacktestRequest) -> None:
    job = _jobs[job_id]
    job.status = "RUNNING"
    try:
        # Run the (synchronous) evaluation in a thread to avoid blocking
        result = await asyncio.get_event_loop().run_in_executor(None, evaluate_strategy, payload)
        job.result = result
        job.status = "COMPLETED"
        job.completed_at = datetime.now(UTC)
        logger.info("backtest job %s completed: %s", job_id, result.status)

        # Publish event if a callback is registered
        if _event_callback is not None:
            try:
                event_data = {
                    "job_id": job_id,
                    "strategy_id": payload.strategy_id,
                    "backtest_status": result.status,
                    "sharpe_ratio": result.sharpe_ratio,
                }
                await _event_callback("backtest.completed", event_data)
            except Exception:
                logger.exception("failed to publish backtest.completed event for job %s", job_id)
    except Exception as exc:
        job.status = "FAILED"
        job.error = str(exc)
        job.completed_at = datetime.now(UTC)
        logger.exception("backtest job %s failed", job_id)
