import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import PROVIDERS, router
from shared.health import check_redis, check_sql, install_health_endpoints

logger = logging.getLogger("llm-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables
    try:
        from app.db.conversation import ensure_tables
        await ensure_tables()
        logger.info("conversation tables ready")
    except Exception as exc:
        logger.warning(f"conversation table init skipped: {exc}")
    yield


app = FastAPI(title="llm-gateway", version="0.2.0", lifespan=lifespan)

_POSTGRES_URL = os.getenv(
    "POSTGRES_URL", "postgresql+psycopg://postgres:postgres@db:5432/platform"
)
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

install_health_endpoints(
    app,
    service="llm-gateway",
    readiness_checks={
        "postgres": lambda: check_sql("postgres", _POSTGRES_URL),
        "redis": lambda: check_redis("redis", _REDIS_URL),
    },
    extra_info={"providers": list(PROVIDERS.keys())},
)
app.include_router(router)
