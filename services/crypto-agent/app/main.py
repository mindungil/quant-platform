from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.services.nats_consumer import consumer


@asynccontextmanager
async def lifespan(_: FastAPI):
    await consumer.start()
    try:
        yield
    finally:
        await consumer.stop()


app = FastAPI(title="crypto-agent", version="0.1.0", lifespan=lifespan)
app.include_router(router)
