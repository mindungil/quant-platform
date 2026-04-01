from fastapi import FastAPI
from app.api.routes import router
from app.core.config import settings
from shared.health import check_redis, check_tcp
from shared.observability import install_http_observability, startup_dependency_guard

app = FastAPI(title="api-gateway", version="0.1.0")
install_http_observability(app, "api-gateway")
app.include_router(router)


@app.on_event("startup")
def startup_checks() -> None:
    startup_dependency_guard(
        service_name="api-gateway",
        check_fns={
            "redis": lambda: check_redis("redis", settings.redis_url),
            "auth-service": lambda: check_tcp("auth-service", settings.auth_service_base_url, default_port=8000),
        },
    )
