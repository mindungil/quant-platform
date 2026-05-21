from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.api.routes import router
from app.services import binance_collector, cross_venue_check, upbit_collector
from app.services.event_publisher import publisher
from shared.health import check_redis, check_sql, check_tcp
from shared.observability import install_http_observability, startup_dependency_guard


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_dependency_guard(
        service_name="market-data",
        check_fns={
            "timescaledb": lambda: check_sql("timescaledb", settings.timescale_url),
            "redis": lambda: check_redis("redis", settings.redis_url),
            "nats": lambda: check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )
    await publisher.connect()
    if binance_collector.is_enabled():
        await binance_collector.start()
    if upbit_collector.is_enabled():
        await upbit_collector.start()
    if cross_venue_check.is_enabled():
        await cross_venue_check.start()
    try:
        yield
    finally:
        await cross_venue_check.stop()
        await upbit_collector.stop()
        await binance_collector.stop()
        await publisher.close()


app = FastAPI(title="market-data", version="0.1.0", lifespan=lifespan)
install_http_observability(app, "market-data")
app.include_router(router)
