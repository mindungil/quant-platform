from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.core.scheduler import scheduler
from app.services.event_publisher import publisher
from app.services.nats_consumer import consumer
from app.services.outcome_consumer import outcome_consumer
from shared.health import check_redis, check_sql, check_tcp
from shared.observability import install_http_observability, startup_dependency_guard


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_dependency_guard(
        service_name="crypto-agent",
        check_fns={
            "postgres": lambda: check_sql("postgres", settings.postgres_url),
            "redis": lambda: check_redis("redis", settings.redis_url),
            "nats": lambda: check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )
    await publisher.connect()
    await consumer.start()
    await outcome_consumer.start()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await outcome_consumer.stop()
        await consumer.stop()
        await publisher.close()


app = FastAPI(title="crypto-agent", version="0.1.0", lifespan=lifespan)
install_http_observability(app, "crypto-agent")
app.include_router(router)
