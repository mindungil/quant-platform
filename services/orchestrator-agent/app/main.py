from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.autonomous_loop import AutonomousLoop, LOOP_ENABLED, LOOP_INTERVAL

_loop = AutonomousLoop(interval_seconds=LOOP_INTERVAL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if LOOP_ENABLED:
        await _loop.start()
    try:
        yield
    finally:
        await _loop.stop()


app = FastAPI(title="orchestrator-agent", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.get("/orchestrator/loop/status")
def loop_status():
    return _loop.get_status()
