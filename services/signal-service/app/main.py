from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.services.event_publisher import publisher
from app.services.nats_consumer import consumer


@asynccontextmanager
async def lifespan(_: FastAPI):
    await publisher.connect()
    await consumer.start()
    try:
        yield
    finally:
        await consumer.stop()
        await publisher.close()


app = FastAPI(title="signal-service", version="0.1.0", lifespan=lifespan)
app.include_router(router)
