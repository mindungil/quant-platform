from fastapi import FastAPI
from app.api.routes import router
from app.core.config import settings
from shared.health import check_redis, check_sql, check_tcp
from shared.observability import install_http_observability, startup_dependency_guard

app = FastAPI(title="risk-service", version="0.1.0")
install_http_observability(app, "risk-service")
app.include_router(router)


@app.on_event("startup")
def startup_checks() -> None:
    startup_dependency_guard(
        service_name="risk-service",
        checks={
            "postgres": check_sql("postgres", settings.postgres_url),
            "redis": check_redis("redis", settings.redis_url),
            "nats": check_tcp("nats", settings.nats_url, default_port=4222),
        },
    )
