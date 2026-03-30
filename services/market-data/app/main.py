from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.services.event_publisher import publisher


@asynccontextmanager
async def lifespan(_: FastAPI):
    await publisher.connect()
    try:
        yield
    finally:
        await publisher.close()


app = FastAPI(title="market-data", version="0.1.0", lifespan=lifespan)
app.include_router(router)
