import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router

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
app.include_router(router)
