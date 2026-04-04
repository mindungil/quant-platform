from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.services.shadow_tracker import shadow_tracker
from app.services.drift_consumer import drift_consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    await shadow_tracker.start()
    await drift_consumer.start()
    yield
    await drift_consumer.stop()
    await shadow_tracker.stop()


app = FastAPI(title="strategy-registry", version="0.1.0", lifespan=lifespan)
app.include_router(router)
