from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.api.routes import router
from app.core.config import settings
from app.core.recovery import recover_stuck_orders
from app.services.event_publisher import publisher
from app.services.nats_consumer import consumer
from app.services import position_monitor
from shared.health import check_redis, check_sql, check_tcp
from shared.logging import get_logger
from shared.observability import install_http_observability, startup_dependency_guard

_logger = get_logger("order-service")


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_dependency_guard(
        service_name="order-service",
        check_fns={
            "postgres": lambda: check_sql("postgres", settings.postgres_url),
            "redis": lambda: check_redis("redis", settings.redis_url),
            "nats": lambda: check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )
    # Recover orders stuck in non-terminal states from before crash/restart
    try:
        recovered = recover_stuck_orders(max_age_seconds=300)
        if recovered:
            _logger.info(
                "startup_recovery_complete",
                extra={"service": "order-service", "recovered_count": recovered},
            )
    except Exception:
        _logger.exception(
            "startup_recovery_error",
            extra={"service": "order-service", "event_type": "order.recovery.startup_error"},
        )
    await publisher.connect()
    await consumer.start()
    await position_monitor.start()
    try:
        yield
    finally:
        await position_monitor.stop()
        await consumer.stop()
        await publisher.close()


app = FastAPI(title="order-service", version="0.1.0", lifespan=lifespan)
install_http_observability(app, "order-service")
app.include_router(router)
