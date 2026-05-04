import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.autonomous_loop import AutonomousLoop, LOOP_ENABLED, LOOP_INTERVAL
from shared.health import check_redis, check_sql, install_health_endpoints

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

_POSTGRES_URL = os.getenv(
    "POSTGRES_URL", "postgresql+psycopg://postgres:postgres@db:5432/platform"
)
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
install_health_endpoints(
    app,
    service="orchestrator-agent",
    readiness_checks={
        "postgres": lambda: check_sql("postgres", _POSTGRES_URL),
        "redis": lambda: check_redis("redis", _REDIS_URL),
    },
)
app.include_router(router)


@app.get("/orchestrator/loop/status")
def loop_status():
    return _loop.get_status()
